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

- [x] 단위 테스트 추가 (24개 테스트 통과)
  - 파일: `packages/selfhealing-python/tests/unit/audit/test_async_audit_pipeline.py`
  - `TestAsyncHealingLoggerNonBlocking` (2개)
  - `TestAsyncHealingLoggerCriticalEvents` (2개)
  - `TestAsyncHealingLoggerBatchFlush` (2개)
  - `TestAsyncHealingLoggerGracefulShutdown` (2개)
  - `TestAsyncAuditLifecycle` (5개)
  - `TestAuditMiddlewareAsyncMode` (3개)
  - `TestConvertEventToDict` (1개)
  - `TestCheckpointManagerIntegration` (3개)
  - `TestAsyncAuditMonitoringMetrics` (4개) - 모니터링 메트릭 테스트 추가
- [x] 통합 테스트 추가 (12개 테스트 통과)
  - 파일: `tests/integration/selfhealing/test_async_audit_middleware_integration.py`
  - `TestAsyncAuditMiddlewareNonBlocking` (2개) - 논블로킹 및 CRITICAL 즉시 전송 테스트
  - `TestAsyncAuditEventRecording` (2개) - 이벤트 기록 및 배치 플러시 테스트
  - `TestAsyncAuditLifecycle` (2개) - 시작/종료 및 그레이스풀 셧다운 테스트
  - `TestAsyncAuditMonitoringMetrics` (4개) - 메트릭 조회 및 Prometheus 포맷 테스트
  - `TestAsyncModeSwitch` (2개) - 비동기 모드 전환 테스트
  - Docker Compose: `docker-compose -f docker-compose.test.yml run --rm test-async-audit`
- [ ] 부하 테스트: 1000 RPS에서 응답 지연 < 10ms 확인

### 6.3 모니터링

- [x] `get_async_audit_metrics()` 함수 구현
  - 파일: `packages/selfhealing-python/src/selfhealing/audit/async_audit_lifecycle.py`
  - 반환 메트릭: `events_logged`, `events_flushed`, `queue_size`, `flush_errors`, `worker_running`
- [x] `export_metrics_to_prometheus()` 함수 구현
  - Prometheus 텍스트 포맷으로 메트릭 출력
  - `selfhealing_async_audit_events_logged_total`
  - `selfhealing_async_audit_events_flushed_total`
  - `selfhealing_async_audit_queue_size`
  - `selfhealing_async_audit_flush_errors_total`
  - `selfhealing_async_audit_worker_running`
- [ ] Grafana 대시보드 추가 (선택사항)
- [ ] AlertManager 알림 규칙 설정 (선택사항)

---

## 7. 예상 효과

| 지표 | Before | After |
|-----|--------|-------|
| 요청당 추가 지연 | 5-50ms | ~0.01ms |
| 이벤트 처리 방식 | 동기 (블로킹) | 비동기 (Non-blocking) |
| DB/Redis 장애 영향 | 요청 실패 | 영향 없음 (Fail-open) |
| 배치 효율 | 없음 (개별 처리) | 10개씩 배치 처리 |

---

## 8. 보완 제안 (코드 리뷰 기반)

> **리뷰일**: 2026-02-03
> **리뷰어**: 코드 분석 결과

현재 구현의 취약점을 분석하고 **총 12가지 보완 제안**을 도출했습니다:
- **9가지 코드 리뷰 기반 제안** (8.1 ~ 8.3)
- **3가지 초기 설계 보완 제안** (8.4)

---

### 8.1 데이터 무결성 및 신뢰성 보완

#### 8.1.1 WAL-First 로깅 (데이터 유실 제로)

**현재 문제점:**

**파일**: `packages/selfhealing-python/src/selfhealing/utils/async_logger.py`
**라인**: 144-154

```python
if severity in cls.IMMEDIATE_SEVERITIES:
    threading.Thread(
        target=cls._flush_immediate, args=([enriched_event],), daemon=True
    ).start()
else:
    cls._queue.put(enriched_event)  # 메모리 큐에만 저장 (WAL 없음)
```

- `AsyncHealingLogger.log()`는 메모리 `queue.Queue`에만 저장
- SIGKILL 시 큐에 있는 모든 이벤트 유실
- `daemon=True` 스레드는 프로세스 종료 시 강제 종료됨

**해결 방안:**

```python
# 파일: packages/selfhealing-python/src/selfhealing/utils/async_logger.py

class WALPolicy(Enum):
    """WAL 기록 정책."""
    ALL = "all"               # 모든 이벤트 WAL 기록
    CRITICAL_ONLY = "critical"  # CRITICAL만 WAL 기록 (권장)
    NONE = "none"             # WAL 미사용 (기존 동작)

class AsyncHealingLogger:
    _wal: WriteAheadLog | None = None
    _wal_policy: WALPolicy = WALPolicy.CRITICAL_ONLY

    @classmethod
    def configure_wal(cls, wal: WriteAheadLog, policy: WALPolicy = WALPolicy.CRITICAL_ONLY) -> None:
        """WAL 인스턴스 및 정책 설정."""
        cls._wal = wal
        cls._wal_policy = policy

    @classmethod
    def log(cls, event: dict[str, Any], severity: EventSeverity = EventSeverity.INFO) -> None:
        enriched_event = {
            **event,
            "severity": severity.name,
            "timestamp": time.time(),
        }

        # WAL-First: 메모리 큐 전에 WAL 기록
        wal_seq = -1
        if cls._wal and cls._should_write_to_wal(severity):
            try:
                wal_seq = cls._wal.write(enriched_event)
            except Exception as e:
                logger.warning(f"[AsyncHealingLogger] WAL write failed: {e}")

        enriched_event["_wal_seq"] = wal_seq

        with cls._lock:
            cls._stats["events_logged"] += 1

        if severity in cls.IMMEDIATE_SEVERITIES:
            cls._critical_executor.submit(cls._flush_immediate, [enriched_event])
        else:
            cls._queue.put(enriched_event)

    @classmethod
    def _should_write_to_wal(cls, severity: EventSeverity) -> bool:
        if cls._wal_policy == WALPolicy.ALL:
            return True
        if cls._wal_policy == WALPolicy.CRITICAL_ONLY:
            return severity in cls.IMMEDIATE_SEVERITIES
        return False
```

**네이밍 결정:**
- `WALPolicy`: 기존 `WALState`, `WALConfig` 패턴과 일관성 유지
- `configure_wal()`: 기존 `configure()` 패턴 확장

**구현 우선순위:** 🔴 **필수** (1순위)

---

#### 8.1.2 주기적 체크포인트 저장

**현재 문제점:**

**파일**: `packages/selfhealing-python/src/selfhealing/audit/async_audit_lifecycle.py`
**라인**: 296-305

```python
def _save_final_checkpoint() -> None:
    """마지막 체크포인트 저장."""
    # Graceful Shutdown 시에만 호출됨
```

- 크래시 시 `last_processed_seq`와 실제 DB 상태 불일치 발생
- 복구 시 중복 처리 또는 유실 가능성

**해결 방안:**

```python
# 파일: packages/selfhealing-python/src/selfhealing/audit/sync_worker.py

@dataclass
class SyncWorkerConfig:
    # 기존 설정...

    # 체크포인트 저장 설정 추가
    checkpoint_save_interval_batches: int = 10  # N 배치마다 저장
    checkpoint_save_interval_seconds: float = 30.0  # 최대 저장 간격

class AuditSyncWorker:
    def __init__(self, ...):
        # 기존 코드...
        self._batches_since_checkpoint = 0
        self._last_checkpoint_time = time.time()

    def _sync_batch(self) -> tuple[int, int]:
        # ... 기존 동기화 로직 ...

        synced_count, failed_count = self._do_sync()

        # 체크포인트 저장 조건 확인
        if synced_count > 0:
            self._batches_since_checkpoint += 1
            should_save_checkpoint = (
                self._batches_since_checkpoint >= self._config.checkpoint_save_interval_batches
                or time.time() - self._last_checkpoint_time >= self._config.checkpoint_save_interval_seconds
            )

            if should_save_checkpoint:
                self._save_checkpoint()
                self._batches_since_checkpoint = 0
                self._last_checkpoint_time = time.time()

        return synced_count, failed_count

    def _save_checkpoint(self) -> None:
        """체크포인트 즉시 저장."""
        try:
            from selfhealing.audit.checkpoint_manager import get_checkpoint_manager
            checkpoint = get_checkpoint_manager()
            checkpoint.save(last_sequence=self._last_processed_seq)
            logger.debug(f"[AuditSyncWorker] Checkpoint saved: seq={self._last_processed_seq}")
        except Exception as e:
            logger.warning(f"[AuditSyncWorker] Checkpoint save failed: {e}")
```

**네이밍 결정:**
- `checkpoint_save_interval_batches`: 기존 `*_interval` 패턴 사용
- `_save_checkpoint()`: 내부 메서드, 단순 명명

**구현 우선순위:** ⚠️ **권장** (3순위)

---

#### 8.1.3 배치 플러시 재시도 로직

**현재 문제점:**

**파일**: `packages/selfhealing-python/src/selfhealing/utils/async_logger.py`
**라인**: 206-212

```python
@classmethod
def _flush_batch(cls, events: list[dict]) -> None:
    try:
        cls._flush_callback(events)
    except Exception as e:
        with cls._lock:
            cls._stats["flush_errors"] += 1
        logger.warning(f"[AsyncHealingLogger] Batch flush failed: {e}")
        # 재시도 없이 드랍!
```

**해결 방안:**

```python
# 파일: packages/selfhealing-python/src/selfhealing/utils/async_logger.py

from dataclasses import dataclass

@dataclass
class FlushRetryConfig:
    """배치 플러시 재시도 설정."""
    max_retries: int = 3
    initial_delay_seconds: float = 1.0
    backoff_multiplier: float = 2.0
    max_delay_seconds: float = 30.0

class AsyncHealingLogger:
    _retry_config: FlushRetryConfig = FlushRetryConfig()
    _retry_queue: queue.Queue = queue.Queue()

    @classmethod
    def _flush_batch_with_retry(cls, events: list[dict], attempt: int = 0) -> None:
        """지수 백오프를 적용한 배치 플러시."""
        if not cls._flush_callback or not events:
            return

        try:
            cls._flush_callback(events)
            with cls._lock:
                cls._stats["events_flushed"] += len(events)
                cls._stats["batch_flushes"] += 1
            logger.debug(f"[AsyncHealingLogger] Flushed {len(events)} events (batch)")

        except Exception as e:
            with cls._lock:
                cls._stats["flush_errors"] += 1

            if attempt < cls._retry_config.max_retries:
                delay = min(
                    cls._retry_config.initial_delay_seconds * (cls._retry_config.backoff_multiplier ** attempt),
                    cls._retry_config.max_delay_seconds,
                )
                with cls._lock:
                    cls._stats["total_retries"] = cls._stats.get("total_retries", 0) + 1

                logger.warning(f"[AsyncHealingLogger] Batch flush failed, retry {attempt + 1} after {delay}s: {e}")

                # 재시도 큐에 추가 (지연 후 재처리)
                cls._retry_queue.put((events, attempt + 1, time.time() + delay))
            else:
                # 최종 실패 - DLQ로 이동 (WAL 시퀀스 유지)
                logger.error(f"[AsyncHealingLogger] Batch flush failed after {attempt} retries, moving to DLQ: {e}")
                cls._move_to_dlq(events, str(e))

    @classmethod
    def _move_to_dlq(cls, events: list[dict], error_message: str) -> None:
        """최종 실패 이벤트를 DLQ로 이동."""
        try:
            from selfhealing.services.dlq import DLQStore
            for event in events:
                DLQStore.store(
                    source="AsyncHealingLogger",
                    payload=event,
                    error_message=error_message,
                    max_retries=0,  # DLQ에서 수동 복구
                )
        except Exception as e:
            logger.error(f"[AsyncHealingLogger] DLQ store failed: {e}")

    @classmethod
    def _worker(cls) -> None:
        """배치 처리 워커 (재시도 큐 포함)."""
        batch: list[dict] = []
        last_flush = time.time()

        while cls._running:
            # 재시도 큐 처리
            cls._process_retry_queue()

            try:
                event = cls._queue.get(timeout=1.0)
                batch.append(event)
            except queue.Empty:
                pass

            # 배치 플러시 조건
            if cls._should_flush(batch, last_flush):
                cls._flush_batch_with_retry(batch)
                batch = []
                last_flush = time.time()

        # 종료 시 남은 이벤트 처리
        if batch:
            cls._flush_batch_with_retry(batch)

    @classmethod
    def _process_retry_queue(cls) -> None:
        """재시도 큐에서 지연된 이벤트 처리."""
        now = time.time()
        while not cls._retry_queue.empty():
            try:
                events, attempt, execute_after = cls._retry_queue.get_nowait()
                if now >= execute_after:
                    cls._flush_batch_with_retry(events, attempt)
                else:
                    # 아직 시간 안됨 - 다시 넣기
                    cls._retry_queue.put((events, attempt, execute_after))
                    break
            except queue.Empty:
                break
```

**네이밍 결정:**
- `FlushRetryConfig`: 기존 `SyncWorkerConfig` 패턴과 일관성
- `_flush_batch_with_retry()`: 기존 `_flush_batch()` 확장
- `_move_to_dlq()`: DLQ 관련 기존 네이밍 패턴

**구현 우선순위:** 🔴 **필수** (2순위)

---

### 8.2 시스템 안정성 및 자원 관리

#### 8.2.1 CRITICAL 이벤트 스레드 풀

**현재 문제점:**

**파일**: `packages/selfhealing-python/src/selfhealing/utils/async_logger.py`
**라인**: 144-149

```python
if severity in cls.IMMEDIATE_SEVERITIES:
    threading.Thread(
        target=cls._flush_immediate, args=([enriched_event],), daemon=True
    ).start()  # 매번 새 스레드 생성!
```

- Alert Storm 시 무제한 스레드 생성
- 메모리 고갈 및 시스템 마비 가능성

**해결 방안:**

```python
# 파일: packages/selfhealing-python/src/selfhealing/utils/async_logger.py

from concurrent.futures import ThreadPoolExecutor

class AsyncHealingLogger:
    # 기존 클래스 변수...
    _critical_executor: ThreadPoolExecutor | None = None
    CRITICAL_EXECUTOR_MAX_WORKERS: int = 5  # 최대 동시 처리 스레드

    @classmethod
    def start(cls) -> None:
        """백그라운드 워커 시작."""
        with cls._lock:
            if cls._running:
                return
            cls._running = True

            # CRITICAL 이벤트 전용 스레드 풀 생성
            cls._critical_executor = ThreadPoolExecutor(
                max_workers=cls.CRITICAL_EXECUTOR_MAX_WORKERS,
                thread_name_prefix="CriticalAuditFlush",
            )

            cls._worker_thread = threading.Thread(target=cls._worker, daemon=True)
            cls._worker_thread.start()
            logger.debug("[AsyncHealingLogger] Background worker started")

    @classmethod
    def stop(cls, timeout: float = 5.0) -> None:
        """백그라운드 워커 중지."""
        with cls._lock:
            if not cls._running:
                return
            cls._running = False

        if cls._worker_thread:
            cls._worker_thread.join(timeout=timeout)

        # 스레드 풀 종료
        if cls._critical_executor:
            cls._critical_executor.shutdown(wait=True, cancel_futures=False)
            cls._critical_executor = None

        logger.debug("[AsyncHealingLogger] Background worker stopped")

    @classmethod
    def log(cls, event: dict[str, Any], severity: EventSeverity = EventSeverity.INFO) -> None:
        # ... 이벤트 enrichment ...

        if severity in cls.IMMEDIATE_SEVERITIES:
            # 스레드 풀 사용 (무제한 스레드 생성 방지)
            if cls._critical_executor:
                cls._critical_executor.submit(cls._flush_immediate, [enriched_event])
            else:
                # Fallback: 스레드 풀 미초기화 시
                threading.Thread(
                    target=cls._flush_immediate, args=([enriched_event],), daemon=True
                ).start()
        else:
            cls._queue.put(enriched_event)
```

**네이밍 결정:**
- `_critical_executor`: `ThreadPoolExecutor` 표준 변수명
- `CriticalAuditFlush`: 스레드 이름 접두사, 디버깅 용이

**구현 우선순위:** 🔴 **필수** (1순위)

---

#### 8.2.2 큐 크기 제한 및 배압 전략

**현재 문제점:**

**파일**: `packages/selfhealing-python/src/selfhealing/utils/async_logger.py`
**라인**: 58

```python
_queue: queue.Queue = queue.Queue()  # maxsize 없음 = 무제한
```

**파일**: `packages/selfhealing-python/src/selfhealing/settings/batch.py`
**라인**: 115-120

```python
async_logger_max_queue_size: int = Field(
    default=5000,
    ge=100,
    le=100000,
    description="AsyncLogger 최대 큐 크기",
)
# 설정은 있으나 실제 적용 안됨!
```

**해결 방안:**

```python
# 파일: packages/selfhealing-python/src/selfhealing/utils/async_logger.py

class QueueOverflowPolicy(Enum):
    """큐 오버플로우 정책."""
    DROP_NEWEST = "drop_newest"  # 새 이벤트 드랍 (기본, 간단)
    DROP_OLDEST = "drop_oldest"  # 오래된 이벤트 드랍 (RingBuffer 방식)
    BLOCK = "block"              # 블로킹 (Non-blocking 위반)

class AsyncHealingLogger:
    _queue: queue.Queue | None = None
    _overflow_policy: QueueOverflowPolicy = QueueOverflowPolicy.DROP_NEWEST

    @classmethod
    def start(cls) -> None:
        with cls._lock:
            if cls._running:
                return

            # 설정에서 max_queue_size 로드
            settings = cls._get_settings()
            max_queue_size = getattr(settings, "async_logger_max_queue_size", 5000)

            cls._queue = queue.Queue(maxsize=max_queue_size)
            cls._running = True
            # ... 나머지 초기화 ...

    @classmethod
    def log(cls, event: dict[str, Any], severity: EventSeverity = EventSeverity.INFO) -> None:
        # ... 이벤트 enrichment ...

        if severity not in cls.IMMEDIATE_SEVERITIES:
            try:
                cls._queue.put_nowait(enriched_event)
            except queue.Full:
                with cls._lock:
                    cls._stats["queue_overflows"] = cls._stats.get("queue_overflows", 0) + 1

                if cls._overflow_policy == QueueOverflowPolicy.DROP_NEWEST:
                    logger.warning("[AsyncHealingLogger] Queue full, dropping newest event")
                elif cls._overflow_policy == QueueOverflowPolicy.DROP_OLDEST:
                    # 가장 오래된 이벤트 제거 후 새 이벤트 추가
                    try:
                        cls._queue.get_nowait()
                        cls._queue.put_nowait(enriched_event)
                    except queue.Empty:
                        pass
                # BLOCK은 put() 사용 (Non-blocking 위반이므로 권장 안함)
```

**네이밍 결정:**
- `QueueOverflowPolicy`: 기존 `WALPolicy` 패턴과 일관성
- `queue_overflows`: 기존 통계 키 패턴

**구현 우선순위:** 🔴 **필수** (2순위)

---

#### 8.2.3 경로 설정 유연화 및 권한 체크

**현재 문제점:**

**파일**: `packages/selfhealing-python/src/selfhealing/audit/checkpoint_manager.py`
**라인**: 69-71

```python
DEFAULT_CHECKPOINT_DIR = "/var/log/audit"  # 하드코딩
DEFAULT_CHECKPOINT_FILENAME = "checkpoint.json"
```

**해결 방안:**

```python
# 파일: packages/selfhealing-python/src/selfhealing/audit/checkpoint_manager.py

import os
import tempfile

class CheckpointManager:
    @staticmethod
    def _get_default_path() -> Path:
        """환경변수 기반 기본 경로 결정."""
        env_path = os.environ.get("SELFHEALING_AUDIT_PATH")
        if env_path:
            return Path(env_path) / "checkpoint.json"

        # OS별 기본 경로
        if os.name == "nt":  # Windows
            return Path(tempfile.gettempdir()) / "selfhealing" / "checkpoint.json"
        else:  # Unix/Linux
            return Path("/var/log/audit") / "checkpoint.json"

    def __init__(self, checkpoint_path: str | Path | None = None, ...):
        if checkpoint_path is None:
            checkpoint_path = self._get_default_path()

        self._path = Path(checkpoint_path)

        # 권한 체크 및 폴백
        if not self._verify_write_permission():
            fallback_path = Path(tempfile.gettempdir()) / "selfhealing" / "checkpoint.json"
            logger.warning(f"[CheckpointManager] No write permission for {self._path}, falling back to {fallback_path}")
            self._path = fallback_path

        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _verify_write_permission(self) -> bool:
        """쓰기 권한 검증."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            test_file = self._path.parent / ".write_test"
            test_file.touch()
            test_file.unlink()
            return True
        except (PermissionError, OSError):
            return False
```

**네이밍 결정:**
- `SELFHEALING_AUDIT_PATH`: 기존 `SELFHEALING_*` 환경변수 패턴
- `_verify_write_permission()`: 명확한 목적 표현

**구현 우선순위:** ⚠️ **권장** (4순위)

---

#### 8.2.4 멀티 프로세스 파일 락

**현재 문제점:**

**파일**: `packages/selfhealing-python/src/selfhealing/audit/checkpoint_manager.py`
**라인**: 120-144

```python
def save(self, last_sequence: int) -> None:
    with self._lock:  # threading.RLock() - 프로세스 내 락만!
        # ... 파일 쓰기 ...
```

**해결 방안:**

```python
# 파일: packages/selfhealing-python/src/selfhealing/audit/checkpoint_manager.py

import os
import sys

# 크로스 플랫폼 파일 락
if sys.platform == "win32":
    import msvcrt

    def lock_file(f):
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)

    def unlock_file(f):
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def lock_file(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def unlock_file(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)

class CheckpointManager:
    def save(self, last_sequence: int) -> None:
        with self._lock:
            checkpoint_data = CheckpointData(
                last_sequence=last_sequence,
                timestamp=time.time(),
            )

            temp_path = self._path.with_suffix(".tmp")
            lock_file_path = self._path.with_suffix(".lock")

            try:
                # 파일 락 획득
                with open(lock_file_path, "w") as lock_f:
                    try:
                        lock_file(lock_f)

                        # 임시 파일에 쓰기
                        with open(temp_path, "w", encoding="utf-8") as f:
                            json.dump(checkpoint_data.to_dict(), f, indent=2)
                            if self._sync_on_write:
                                f.flush()
                                os.fsync(f.fileno())

                        # 원자적 rename
                        temp_path.replace(self._path)

                    finally:
                        unlock_file(lock_f)

                logger.debug(f"Checkpoint saved: sequence={last_sequence}")

            except (BlockingIOError, OSError) as e:
                # 다른 프로세스가 락 보유 중 - 스킵
                logger.warning(f"[CheckpointManager] Lock contention, skipping save: {e}")

            except Exception as e:
                try:
                    temp_path.unlink(missing_ok=True)
                except Exception:
                    pass
                raise CheckpointError(f"Failed to save checkpoint: {e}") from e
```

**대안 - PID 기반 분리:**

```python
# 각 프로세스가 별도 체크포인트 파일 사용
def _get_checkpoint_filename(self) -> str:
    return f"checkpoint_{os.getpid()}.json"
```

**네이밍 결정:**
- `lock_file()`, `unlock_file()`: 표준 유틸리티 함수명
- `.lock` 확장자: 락 파일 표준 관례

**구현 우선순위:** 🔴 **필수** (3순위)

---

### 8.3 모니터링 및 성능 최적화

#### 8.3.1 고속 직렬화 라이브러리

**현재 상태:**

**파일**: `packages/selfhealing-python/src/selfhealing/audit/wal.py`
**라인**: 347

```python
entry_bytes = json.dumps(entry, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
```

**해결 방안:**

```python
# 파일: packages/selfhealing-python/src/selfhealing/utils/serialization.py (신규)

"""
고속 JSON 직렬화 유틸리티.

orjson 사용 가능 시 자동 활용, 없으면 표준 json 폴백.
"""

from typing import Any

try:
    import orjson

    def fast_dumps(obj: Any) -> bytes:
        """고속 JSON 직렬화 (bytes 반환)."""
        return orjson.dumps(obj)

    def fast_loads(data: bytes | str) -> Any:
        """고속 JSON 역직렬화."""
        return orjson.loads(data)

    FAST_JSON_AVAILABLE = True

except ImportError:
    import json

    def fast_dumps(obj: Any) -> bytes:
        """표준 JSON 직렬화 (bytes 반환)."""
        return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    def fast_loads(data: bytes | str) -> Any:
        """표준 JSON 역직렬화."""
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        return json.loads(data)

    FAST_JSON_AVAILABLE = False


# 사용 예시 (wal.py)
from selfhealing.utils.serialization import fast_dumps

entry_bytes = fast_dumps(entry)
checksum = self._compute_checksum(entry_bytes)
```

**네이밍 결정:**
- `fast_dumps()`, `fast_loads()`: 기존 `json.dumps/loads` 대응
- `FAST_JSON_AVAILABLE`: 조건부 로직용 플래그

**구현 우선순위:** ✅ **선택** (5순위)

---

#### 8.3.2 에러 임계치 기반 자동 알림

**현재 문제점:**

**파일**: `packages/selfhealing-python/src/selfhealing/utils/async_logger.py`
**라인**: 70-76

```python
_stats = {
    "events_logged": 0,
    "events_flushed": 0,
    "flush_errors": 0,  # 카운터만 있고 알림 없음
    ...
}
```

**해결 방안:**

```python
# 파일: packages/selfhealing-python/src/selfhealing/utils/async_logger.py

from dataclasses import dataclass
from collections import deque

@dataclass
class FlushErrorAlertConfig:
    """플러시 에러 알림 설정."""
    threshold_count: int = 10        # 임계치 (N회)
    window_seconds: float = 60.0     # 시간 윈도우 (초)
    cooldown_seconds: float = 300.0  # 알림 쿨다운 (5분)
    severity: str = "CRITICAL"       # 알림 등급

class AsyncHealingLogger:
    _error_timestamps: deque = deque(maxlen=100)  # 최근 에러 타임스탬프
    _last_alert_time: float = 0
    _alert_config: FlushErrorAlertConfig = FlushErrorAlertConfig()

    @classmethod
    def _check_and_send_alert(cls) -> None:
        """에러 임계치 확인 및 알림 발송."""
        now = time.time()

        # 쿨다운 체크
        if now - cls._last_alert_time < cls._alert_config.cooldown_seconds:
            return

        # 시간 윈도우 내 에러 수 계산
        window_start = now - cls._alert_config.window_seconds
        recent_errors = sum(1 for ts in cls._error_timestamps if ts >= window_start)

        if recent_errors >= cls._alert_config.threshold_count:
            cls._send_flush_error_alert(recent_errors)
            cls._last_alert_time = now

    @classmethod
    def _send_flush_error_alert(cls, error_count: int) -> None:
        """UnifiedNotificationManager를 통한 알림 발송."""
        try:
            from selfhealing.services.unified_notification import (
                UnifiedNotificationManager,
                NotificationSeverity,
            )

            manager = UnifiedNotificationManager()
            manager.notify(
                title="[AsyncAuditLogger] 플러시 에러 임계치 초과",
                message=f"{cls._alert_config.window_seconds}초 내 {error_count}회 플러시 실패. "
                        f"데이터 유실 위험 - 즉시 확인 필요",
                severity=NotificationSeverity[cls._alert_config.severity],
                source="AsyncHealingLogger",
                details={
                    "error_count": error_count,
                    "threshold": cls._alert_config.threshold_count,
                    "window_seconds": cls._alert_config.window_seconds,
                    "queue_size": cls._queue.qsize() if cls._queue else 0,
                },
            )
            logger.info(f"[AsyncHealingLogger] Flush error alert sent: {error_count} errors")

        except Exception as e:
            logger.error(f"[AsyncHealingLogger] Failed to send alert: {e}")

    @classmethod
    def _flush_batch(cls, events: list[dict]) -> None:
        try:
            cls._flush_callback(events)
            # ... 성공 로직 ...

        except Exception as e:
            with cls._lock:
                cls._stats["flush_errors"] += 1
                cls._error_timestamps.append(time.time())

            logger.warning(f"[AsyncHealingLogger] Batch flush failed: {e}")

            # 알림 체크
            cls._check_and_send_alert()
```

**네이밍 결정:**
- `FlushErrorAlertConfig`: 기존 `*Config` 패턴
- `_check_and_send_alert()`: 내부 메서드, 명확한 목적
- `_error_timestamps`: 슬라이딩 윈도우용

**구현 우선순위:** 🔴 **필수** (4순위)

---

### 8.4 초기 설계 보완 제안 (3가지)

> 아래 3가지는 초기 리뷰에서 제안된 핵심 개선사항입니다.

#### 8.4.1 Priority Queue 도입 (CRITICAL 이벤트 우선 처리)

**현재 문제점:**

**파일**: `packages/selfhealing-python/src/selfhealing/utils/async_logger.py`
**라인**: 58, 144-154

```python
_queue: queue.Queue = queue.Queue()  # 일반 FIFO 큐

if severity in cls.IMMEDIATE_SEVERITIES:
    # CRITICAL: 별도 스레드 생성 (오버헤드)
    threading.Thread(...).start()
else:
    # 일반: FIFO 큐에 추가 (우선순위 없음)
    cls._queue.put(enriched_event)
```

- CRITICAL 이벤트마다 스레드 생성 → Alert Storm 시 시스템 마비
- 일반 큐는 우선순위 개념 없음

**해결 방안:**

```python
# 파일: packages/selfhealing-python/src/selfhealing/utils/async_logger.py

from queue import PriorityQueue
from dataclasses import dataclass, field
from typing import Any

@dataclass(order=True)
class PrioritizedEvent:
    """우선순위 기반 이벤트 래퍼."""
    priority: int  # 낮을수록 높은 우선순위
    timestamp: float = field(compare=False)
    event: dict[str, Any] = field(compare=False)

class EventPriority:
    """이벤트 우선순위 상수."""
    CRITICAL = 0   # 최고 우선순위 (즉시 처리)
    WARNING = 1
    INFO = 2
    DEBUG = 3

# Severity → Priority 매핑
SEVERITY_PRIORITY_MAP = {
    EventSeverity.CRITICAL: EventPriority.CRITICAL,
    EventSeverity.WARNING: EventPriority.WARNING,
    EventSeverity.INFO: EventPriority.INFO,
    EventSeverity.DEBUG: EventPriority.DEBUG,
}

class AsyncHealingLogger:
    _priority_queue: PriorityQueue = PriorityQueue()

    @classmethod
    def log(cls, event: dict[str, Any], severity: EventSeverity = EventSeverity.INFO) -> None:
        enriched_event = {
            **event,
            "severity": severity.name,
            "timestamp": time.time(),
        }

        priority = SEVERITY_PRIORITY_MAP.get(severity, EventPriority.INFO)
        prioritized = PrioritizedEvent(
            priority=priority,
            timestamp=time.time(),
            event=enriched_event,
        )

        with cls._lock:
            cls._stats["events_logged"] += 1

        # 모든 이벤트를 우선순위 큐에 추가 (스레드 생성 없음)
        cls._priority_queue.put(prioritized)

    @classmethod
    def _worker(cls) -> None:
        """우선순위 기반 배치 처리 워커."""
        batch: list[dict] = []
        critical_batch: list[dict] = []
        last_flush = time.time()

        while cls._running:
            try:
                prioritized = cls._priority_queue.get(timeout=0.1)

                if prioritized.priority == EventPriority.CRITICAL:
                    # CRITICAL은 별도 배치로 즉시 처리
                    critical_batch.append(prioritized.event)
                else:
                    batch.append(prioritized.event)

            except queue.Empty:
                pass

            # CRITICAL 배치 즉시 플러시
            if critical_batch:
                cls._flush_batch_with_retry(critical_batch)
                critical_batch = []

            # 일반 배치 조건부 플러시
            if cls._should_flush(batch, last_flush):
                cls._flush_batch_with_retry(batch)
                batch = []
                last_flush = time.time()
```

**네이밍 결정:**
- `PrioritizedEvent`: 우선순위 포함 이벤트 래퍼
- `EventPriority`: 우선순위 상수 클래스 (기존 `EventSeverity`와 구분)
- `_priority_queue`: 기존 `_queue` 대체

**스레드 풀과의 관계:**
- Priority Queue: 단일 워커에서 우선순위 처리
- ThreadPoolExecutor: 병렬 처리 (8.2.1)
- **둘 다 적용 가능** - Priority Queue가 더 가벼움

**구현 우선순위:** 🔴 **필수** (1순위 - 8.2.1 대안)

---

#### 8.4.2 WAL 기록 보장 핸들러 (Durable Logging)

**현재 문제점:**

현재 데이터 흐름:
```
AuditMiddleware
    → AsyncHealingLogger._queue (메모리만)
    → flush_callback → AuditAdapter.log()
```

WAL과 분리됨:
```
WAL.write() ← AuditSyncWorker가 별도로 관리
```

- 메모리 큐와 WAL이 연결되지 않음
- SIGKILL 시 큐 데이터 전부 유실

**해결 방안:**

```python
# 파일: packages/selfhealing-python/src/selfhealing/utils/durable_logger.py (신규)

"""
DurableEventLogger - WAL 보장 비동기 로거.

AsyncHealingLogger의 WAL 통합 확장 버전.
모든 이벤트가 WAL에 먼저 기록된 후 큐에 추가됨.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any

from selfhealing.audit.wal import WriteAheadLog
from selfhealing.utils.async_logger import AsyncHealingLogger, EventSeverity

logger = logging.getLogger(__name__)


class DurableEventLogger(AsyncHealingLogger):
    """
    WAL 보장 비동기 로거.

    AsyncHealingLogger를 상속하며 WAL-First 정책을 강제합니다.
    """

    _wal: WriteAheadLog | None = None
    _wal_enabled: bool = True

    @classmethod
    def configure_wal(cls, wal: WriteAheadLog) -> None:
        """WAL 인스턴스 설정."""
        cls._wal = wal
        logger.info("[DurableEventLogger] WAL configured")

    @classmethod
    def log(cls, event: dict[str, Any], severity: EventSeverity = EventSeverity.INFO) -> None:
        """
        WAL-First 이벤트 로깅.

        순서:
        1. WAL에 기록 (디스크 영속화)
        2. 메모리 큐에 추가 (배치 처리용)
        """
        enriched_event = {
            **event,
            "severity": severity.name,
            "timestamp": time.time(),
        }

        # 1. WAL-First: 디스크에 먼저 기록
        wal_seq = -1
        if cls._wal and cls._wal_enabled:
            try:
                wal_seq = cls._wal.write(enriched_event)
                enriched_event["_wal_seq"] = wal_seq
            except Exception as e:
                logger.warning(f"[DurableEventLogger] WAL write failed (continuing): {e}")
                # Fail-Open: WAL 실패해도 계속 진행

        with cls._lock:
            cls._stats["events_logged"] += 1
            if wal_seq > 0:
                cls._stats["wal_writes"] = cls._stats.get("wal_writes", 0) + 1

        # 2. 메모리 큐에 추가 (부모 클래스 로직)
        if severity in cls.IMMEDIATE_SEVERITIES:
            if cls._critical_executor:
                cls._critical_executor.submit(cls._flush_immediate, [enriched_event])
            else:
                threading.Thread(
                    target=cls._flush_immediate, args=([enriched_event],), daemon=True
                ).start()
        else:
            try:
                cls._queue.put_nowait(enriched_event)
            except queue.Full:
                with cls._lock:
                    cls._stats["queue_overflows"] = cls._stats.get("queue_overflows", 0) + 1
                logger.warning("[DurableEventLogger] Queue full, event in WAL only")

    @classmethod
    def recover_from_wal(cls, last_processed_seq: int = 0) -> int:
        """
        WAL에서 미처리 이벤트 복구.

        재시작 시 호출하여 WAL의 미처리 이벤트를 큐에 재추가.

        Returns:
            복구된 이벤트 수
        """
        if not cls._wal:
            return 0

        try:
            entries = cls._wal.recover_unprocessed(last_processed_seq)
            for entry in entries:
                cls._queue.put(entry.data)

            logger.info(f"[DurableEventLogger] Recovered {len(entries)} events from WAL")
            return len(entries)
        except Exception as e:
            logger.error(f"[DurableEventLogger] WAL recovery failed: {e}")
            return 0
```

**네이밍 결정:**
- `DurableEventLogger`: "내구성 있는" 로거 (WAL 보장)
- 기존 `AsyncHealingLogger`를 상속하여 확장
- `recover_from_wal()`: 복구 메서드 추가

**8.1.1과의 관계:**
- 8.1.1은 기존 클래스에 WAL 기능 추가
- 8.4.2는 별도 클래스로 분리 (선택적 사용)
- **둘 중 하나 선택** - 8.4.2가 더 깔끔한 설계

**구현 우선순위:** 🔴 **필수** (1순위 - 8.1.1 대안)

---

#### 8.4.3 Batch Retry 로직 강화 (지수 백오프 + DLQ)

**현재 문제점:**

**파일**: `packages/selfhealing-python/src/selfhealing/utils/async_logger.py`
**라인**: 206-212

```python
except Exception as e:
    with cls._lock:
        cls._stats["flush_errors"] += 1
    logger.warning(f"[AsyncHealingLogger] Batch flush failed: {e}")
    # 데이터 드랍! 재시도 없음!
```

**기존 패턴 참고:**

**파일**: `packages/selfhealing-python/src/selfhealing/audit/sync_worker.py`
**라인**: 433-474 (이미 구현된 지수 백오프)

```python
delay = self._config.retry_delay_seconds
for attempt in range(self._config.max_retries + 1):
    try:
        adapter.write(entry.data)
        return
    except Exception as e:
        if attempt < self._config.max_retries:
            time.sleep(delay)
            delay = min(
                delay * self._config.retry_backoff_multiplier,
                self._config.max_retry_delay_seconds,
            )
```

**해결 방안:**

```python
# 파일: packages/selfhealing-python/src/selfhealing/utils/async_logger.py

from dataclasses import dataclass
from typing import Callable

@dataclass
class BatchRetryPolicy:
    """배치 재시도 정책 (지수 백오프)."""
    max_retries: int = 3
    initial_delay_seconds: float = 1.0
    backoff_multiplier: float = 2.0
    max_delay_seconds: float = 30.0
    dlq_on_final_failure: bool = True  # 최종 실패 시 DLQ 이동

class AsyncHealingLogger:
    _retry_policy: BatchRetryPolicy = BatchRetryPolicy()
    _pending_retries: list[tuple[list[dict], int, float]] = []  # (events, attempt, next_retry_time)

    @classmethod
    def configure_retry(cls, policy: BatchRetryPolicy) -> None:
        """재시도 정책 설정."""
        cls._retry_policy = policy

    @classmethod
    def _flush_batch(cls, events: list[dict]) -> None:
        """배치 플러시 (재시도 지원)."""
        cls._flush_with_retry(events, attempt=0)

    @classmethod
    def _flush_with_retry(cls, events: list[dict], attempt: int) -> None:
        """지수 백오프를 적용한 배치 플러시."""
        if not cls._flush_callback or not events:
            return

        try:
            cls._flush_callback(events)
            with cls._lock:
                cls._stats["events_flushed"] += len(events)
                cls._stats["batch_flushes"] += 1
            logger.debug(f"[AsyncHealingLogger] Flushed {len(events)} events")

        except Exception as e:
            with cls._lock:
                cls._stats["flush_errors"] += 1

            if attempt < cls._retry_policy.max_retries:
                # 재시도 스케줄링
                delay = min(
                    cls._retry_policy.initial_delay_seconds * (cls._retry_policy.backoff_multiplier ** attempt),
                    cls._retry_policy.max_delay_seconds,
                )
                next_retry = time.time() + delay

                with cls._lock:
                    cls._pending_retries.append((events, attempt + 1, next_retry))
                    cls._stats["pending_retries"] = len(cls._pending_retries)

                logger.warning(
                    f"[AsyncHealingLogger] Flush failed, retry {attempt + 1}/{cls._retry_policy.max_retries} "
                    f"after {delay:.1f}s: {e}"
                )
            else:
                # 최종 실패
                logger.error(f"[AsyncHealingLogger] Flush failed after {attempt} retries: {e}")

                if cls._retry_policy.dlq_on_final_failure:
                    cls._move_to_dlq(events, str(e))
                else:
                    # WAL에 시퀀스가 있으면 SyncWorker가 재처리
                    logger.warning("[AsyncHealingLogger] Events lost (no DLQ, check WAL)")

    @classmethod
    def _process_pending_retries(cls) -> None:
        """대기 중인 재시도 처리 (워커에서 주기적 호출)."""
        now = time.time()
        remaining = []

        with cls._lock:
            retries = cls._pending_retries[:]
            cls._pending_retries = []

        for events, attempt, next_retry in retries:
            if now >= next_retry:
                cls._flush_with_retry(events, attempt)
            else:
                remaining.append((events, attempt, next_retry))

        with cls._lock:
            cls._pending_retries.extend(remaining)
            cls._stats["pending_retries"] = len(cls._pending_retries)

    @classmethod
    def _move_to_dlq(cls, events: list[dict], error_message: str) -> None:
        """최종 실패 이벤트를 DLQ로 이동."""
        try:
            from selfhealing.services.dlq.store_operations import DLQStoreOperations

            dlq = DLQStoreOperations()
            for event in events:
                dlq.store(
                    source="AsyncHealingLogger",
                    payload=event,
                    error_message=error_message,
                    max_retries=0,
                )

            with cls._lock:
                cls._stats["dlq_moved"] = cls._stats.get("dlq_moved", 0) + len(events)

            logger.info(f"[AsyncHealingLogger] Moved {len(events)} events to DLQ")

        except Exception as e:
            logger.error(f"[AsyncHealingLogger] DLQ store failed: {e}")

    @classmethod
    def _worker(cls) -> None:
        """배치 처리 워커 (재시도 포함)."""
        batch: list[dict] = []
        last_flush = time.time()

        while cls._running:
            # 1. 대기 중인 재시도 처리
            cls._process_pending_retries()

            # 2. 새 이벤트 수집
            try:
                event = cls._queue.get(timeout=0.5)
                batch.append(event)
            except queue.Empty:
                pass

            # 3. 배치 플러시
            if cls._should_flush(batch, last_flush):
                cls._flush_batch(batch)
                batch = []
                last_flush = time.time()

        # 종료 시 남은 이벤트 처리
        if batch:
            cls._flush_batch(batch)

        # 남은 재시도도 처리
        for events, attempt, _ in cls._pending_retries:
            cls._flush_with_retry(events, attempt)
```

**네이밍 결정:**
- `BatchRetryPolicy`: 기존 `*Policy` 패턴
- `_pending_retries`: 대기 중인 재시도 목록
- `_flush_with_retry()`: 재시도 포함 플러시
- `_process_pending_retries()`: 워커에서 호출

**8.1.3과의 관계:**
- 8.1.3은 간략한 설명
- 8.4.3은 상세 구현 코드 포함
- **동일 기능, 8.4.3이 더 상세함**

**구현 우선순위:** 🔴 **필수** (2순위)

---

### 8.5 구현 순서 및 체크리스트 (총 12개 항목)

| 순위 | 항목 | 파일 | 예상 소요 | 의존성 |
|-----|------|------|----------|--------|
| 1 | Priority Queue 도입 (8.4.1) | `async_logger.py` | 0.5일 | 없음 |
| 1 | WAL-First 로깅 / DurableEventLogger (8.1.1, 8.4.2) | `async_logger.py`, `durable_logger.py` | 1일 | WAL |
| 1 | CRITICAL 스레드 풀 (8.2.1) | `async_logger.py` | 0.5일 | 없음 |
| 2 | 배치 플러시 재시도 (8.1.3, 8.4.3) | `async_logger.py` | 1일 | 없음 |
| 2 | 큐 크기 제한 (8.2.2) | `async_logger.py` | 0.5일 | 없음 |
| 3 | 멀티 프로세스 파일 락 (8.2.4) | `checkpoint_manager.py` | 0.5일 | 없음 |
| 3 | 주기적 체크포인트 (8.1.2) | `sync_worker.py` | 0.5일 | 없음 |
| 4 | 에러 알림 (8.3.2) | `async_logger.py` | 0.5일 | UnifiedNotificationManager |
| 4 | 경로 유연화 (8.2.3) | `checkpoint_manager.py` | 0.5일 | 없음 |
| 5 | 고속 직렬화 (8.3.1) | `serialization.py` (신규) | 0.5일 | orjson (선택) |

**총 항목: 12개 (9가지 리뷰 + 3가지 초기 보완 제안)**
**총 예상 소요: 6.5일**

---

### 8.6 테스트 추가 계획

```python
# 파일: tests/unit/audit/test_async_audit_resilience.py

class TestWALFirstLogging:
    """WAL-First 로깅 테스트."""

    def test_wal_written_before_queue(self):
        """WAL이 큐 삽입 전에 기록되는지 확인."""
        pass

    def test_critical_events_always_wal(self):
        """CRITICAL 이벤트는 항상 WAL에 기록."""
        pass

class TestFlushRetry:
    """배치 플러시 재시도 테스트."""

    def test_exponential_backoff(self):
        """지수 백오프 적용 확인."""
        pass

    def test_dlq_on_final_failure(self):
        """최종 실패 시 DLQ 이동."""
        pass

class TestQueueBackpressure:
    """큐 배압 테스트."""

    def test_drop_newest_on_full(self):
        """큐 가득 시 새 이벤트 드랍."""
        pass

    def test_queue_overflow_metric(self):
        """오버플로우 메트릭 기록."""
        pass

class TestFlushErrorAlert:
    """플러시 에러 알림 테스트."""

    def test_alert_on_threshold(self):
        """임계치 초과 시 알림 발송."""
        pass

    def test_cooldown_prevents_spam(self):
        """쿨다운으로 알림 스팸 방지."""
        pass


class TestPriorityQueue:
    """Priority Queue 테스트 (8.4.1)."""

    def test_critical_events_processed_first(self):
        """CRITICAL 이벤트가 INFO보다 먼저 처리."""
        pass

    def test_no_thread_creation_on_critical(self):
        """CRITICAL 이벤트에 별도 스레드 생성 안함."""
        pass


class TestDurableEventLogger:
    """DurableEventLogger 테스트 (8.4.2)."""

    def test_wal_written_before_queue(self):
        """WAL이 큐 삽입 전에 기록."""
        pass

    def test_recover_from_wal_on_restart(self):
        """재시작 시 WAL에서 복구."""
        pass


class TestBatchRetryPolicy:
    """Batch Retry Policy 테스트 (8.4.3)."""

    def test_pending_retries_processed(self):
        """대기 중인 재시도가 처리됨."""
        pass

    def test_max_retries_moves_to_dlq(self):
        """최대 재시도 후 DLQ로 이동."""
        pass
```

---

## 8.5 구현 완료 상태

> **구현일**: 2026-02-03
> **상태**: ✅ 완료
> **테스트**: 38개 테스트 통과 (test_async_audit_resilience.py)

### 8.5.1 구현된 파일 목록

| 파일 | 구현 내용 |
|-----|---------|
| `async_logger.py` | WALPolicy, QueueOverflowPolicy, EventPriority 열거형, BatchRetryPolicy/FlushErrorAlertConfig/PrioritizedEvent 데이터클래스, Priority Queue, WAL-First 로깅, Batch Retry, Queue Backpressure, Flush 에러 알림, ThreadPoolExecutor |
| `durable_logger.py` | DurableEventLogger 클래스 (8.4.2) |
| `sync_worker.py` | 주기적 체크포인트 저장 설정 (8.1.2) |
| `checkpoint_manager.py` | 경로 유연성 (8.2.3), 멀티 프로세스 파일 잠금 (8.2.4) |
| `serialization.py` | 빠른 직렬화 유틸리티 (8.3.1) |
| `test_async_audit_resilience.py` | 38개 단위 테스트 |

### 8.5.2 구현 완료 항목

| 항목 | 섹션 | 상태 |
|-----|------|------|
| WAL-First 로깅 | 8.1.1 | ✅ |
| 주기적 Checkpoint 저장 | 8.1.2 | ✅ |
| Batch Retry 로직 | 8.1.3 | ✅ |
| 전용 ThreadPool | 8.2.1 | ✅ |
| Queue Backpressure | 8.2.2 | ✅ |
| 경로 유연성 | 8.2.3 | ✅ |
| 멀티 프로세스 파일 잠금 | 8.2.4 | ✅ |
| 빠른 직렬화 | 8.3.1 | ✅ |
| Flush 에러 알림 | 8.3.2 | ✅ |
| Priority Queue | 8.4.1 | ✅ |
| DurableEventLogger | 8.4.2 | ✅ |
| DLQ 이동 (Batch Retry) | 8.4.3 | ✅ |

---

## 9. 다음 단계

→ [168_REDIS_BATCH_OPTIMIZATION.md](168_REDIS_BATCH_OPTIMIZATION.md): Redis 배치 처리 최적화
