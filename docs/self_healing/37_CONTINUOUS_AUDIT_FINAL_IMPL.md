# 37. Continuous Audit 최종 구현 문서 v2

## 📋 Executive Summary

리뷰어 피드백과 코드베이스 분석 결과를 종합한 최종 구현 계획서입니다.

**핵심 원칙:**
1. **비침투성(Non-intrusion)**: 메인 애플리케이션에 영향 없이 작동
2. **최소 의존성(Minimal Dependencies)**: 외부 의존성 최소화
3. **Raw Data 출력**: 포맷팅 없이 원본 데이터 제공 (조직별 보고서 형식 대응)

---

## 🔍 현황 분석: 있는 것 vs 없는 것

### ✅ 이미 구현된 기능 (재사용 가능)

| 기능 | 위치 | 설명 | 상태 |
|------|------|------|------|
| **Circuit Breaker** | `resilience.py#L28-L200` | 3단계 상태 (CLOSED/OPEN/HALF_OPEN), Registry 포함 | ✅ 완전 구현 |
| **Syslog Fallback** | `resilience.py#L462-L550` | OS-level 최후의 수단, Windows/Linux 대응 | ✅ 완전 구현 |
| **Audit Metrics** | `resilience.py#L280-L400` | Prometheus-compatible 메트릭 | ✅ 완전 구현 |
| **Degraded Mode** | `resilience.py#L350-L460` | 자동 fallback 관리 | ✅ 완전 구현 |
| **AsyncHealingLogger** | `async_logger.py` | 비동기 이벤트 버퍼링, 배치 처리 | ✅ 완전 구현 |
| **PoolWatchdog** | `pool_watchdog.py` | 자동 복구 액션 패턴 | ✅ 완전 구현 |
| **Rate Limiter** | `rate_limit.py` | L1/L2 Defense-in-Depth | ✅ 완전 구현 |
| **Backpressure Check** | `memory_test_views.py` | check_backpressure() 함수 | ✅ 완전 구현 |
| **Checksum** | `stage35_cache_poison.py` | CRC32 체크섬 검증 | ✅ 테스트 구현 |

### ❌ 구현 필요 (신규 개발)

| 기능 | 우선순위 | 설명 | 예상 공수 |
|------|----------|------|-----------|
| **RingBuffer** | P0 | Drop Oldest 전략 메모리 버퍼 | 2h |
| **WAL (Write-Ahead Log)** | P0 | 데이터 무결성 보장 + Checksum | 4h |
| **Self-Audit** | P1 | 감사 시스템 자체 실패 로깅 | 2h |
| **Audit Watchdog** | P1 | Dead Man's Switch 패턴 | 2h |
| **Hash Chain Verifier** | P2 | 기록 무결성 검증 CLI 도구 | 4h |

### 🔗 연결 필요 (통합 작업)

| 기능 | 현황 | 필요 작업 | 예상 공수 |
|------|------|-----------|-----------|
| **ContinuousAuditRecorder ↔ CircuitBreaker** | 미연결 | resilience.py의 CB를 audit recorder에 통합 | 2h |
| **ContinuousAuditRecorder ↔ SyslogFallback** | 미연결 | 실패 시 자동 syslog 연결 | 1h |
| **AsyncLogger ↔ ContinuousAudit** | 별도 구현 | 인터페이스 통합 또는 어댑터 | 2h |

---

## 🏗️ 아키텍처 설계

```
┌────────────────────────────────────────────────────────────────────┐
│                    Continuous Audit System                         │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│  ┌─────────────┐   ┌──────────────┐   ┌─────────────────────────┐ │
│  │ Application │──▶│ Shadow Queue │──▶│ Background Flush Worker │ │
│  │ (Non-Block) │   │ (RingBuffer) │   │ (Async)                 │ │
│  └─────────────┘   └──────────────┘   └───────────┬─────────────┘ │
│                          │                        │                │
│                   Backpressure              ┌─────▼─────┐          │
│                   (Drop Oldest)             │   WAL     │          │
│                                             │(Checksum) │          │
│                                             └─────┬─────┘          │
│  ┌───────────────────────────────────────────────┼────────────────┐│
│  │                  Write Path                   │                ││
│  │  ┌──────────────┐   ┌──────────────┐   ┌─────▼─────┐          ││
│  │  │Circuit Breaker│──▶│ Primary Store│   │  Local DB │          ││
│  │  │  (EXISTING)  │   │ (PostgreSQL) │   │ (SQLite)  │          ││
│  │  └──────┬───────┘   └──────────────┘   └───────────┘          ││
│  │         │                                                      ││
│  │         │ OPEN                                                 ││
│  │         ▼                                                      ││
│  │  ┌──────────────┐   ┌──────────────┐   ┌───────────┐          ││
│  │  │Degraded Mode │──▶│ Local File   │──▶│  Syslog   │          ││
│  │  │  (EXISTING)  │   │ (JSON Lines) │   │(EXISTING) │          ││
│  │  └──────────────┘   └──────────────┘   └───────────┘          ││
│  └────────────────────────────────────────────────────────────────┘│
│                                                                    │
│  ┌────────────────────────────────────────────────────────────────┐│
│  │                     Self-Monitoring (NEW)                      ││
│  │  ┌──────────────┐   ┌──────────────┐   ┌───────────────┐      ││
│  │  │ Audit Watchdog│   │ Self-Audit   │   │ Rate Limiter  │      ││
│  │  │(Heartbeat)   │   │ Logger       │   │ (EXISTING)    │      ││
│  │  └──────────────┘   └──────────────┘   └───────────────┘      ││
│  └────────────────────────────────────────────────────────────────┘│
└────────────────────────────────────────────────────────────────────┘
```

---

## 📦 신규 구현 명세

### 1. RingBuffer (신규 구현) - `ring_buffer.py`

**리뷰어 조언:**
> "Shadow Logging의 Backpressure 전략: Drop Oldest vs Block"

```python
"""
Ring Buffer with Backpressure for Shadow Logging.

비침투 원칙에 따라 DROP_OLDEST가 기본값.
메인 애플리케이션 성능에 영향을 주지 않음.
"""

from collections import deque
from threading import Lock
from typing import TypeVar, Generic, List
from dataclasses import dataclass
from enum import Enum

T = TypeVar('T')


class BackpressureStrategy(Enum):
    """배압 전략"""
    DROP_OLDEST = "drop_oldest"  # 권장: 비침투
    DROP_NEWEST = "drop_newest"


@dataclass
class RingBufferStats:
    """버퍼 통계"""
    capacity: int
    size: int
    total_enqueued: int
    total_dropped: int
    drop_rate: float


class RingBuffer(Generic[T]):
    """Thread-Safe Ring Buffer with Backpressure."""
    
    def __init__(
        self,
        capacity: int = 10000,
        strategy: BackpressureStrategy = BackpressureStrategy.DROP_OLDEST,
    ):
        self._capacity = capacity
        self._strategy = strategy
        self._buffer: deque = deque(maxlen=capacity)
        self._lock = Lock()
        self._total_enqueued = 0
        self._total_dropped = 0
    
    def put(self, item: T) -> bool:
        """아이템 추가. 논블로킹. Returns True if added."""
        with self._lock:
            if len(self._buffer) >= self._capacity:
                if self._strategy == BackpressureStrategy.DROP_OLDEST:
                    self._buffer.popleft()
                    self._total_dropped += 1
                else:
                    self._total_dropped += 1
                    return False
            
            self._buffer.append(item)
            self._total_enqueued += 1
            return True
    
    def get_batch(self, max_size: int = 100) -> List[T]:
        """배치 조회 및 제거"""
        with self._lock:
            batch = []
            for _ in range(min(max_size, len(self._buffer))):
                if self._buffer:
                    batch.append(self._buffer.popleft())
            return batch
    
    def get_stats(self) -> RingBufferStats:
        """통계 조회"""
        with self._lock:
            size = len(self._buffer)
            drop_rate = (
                self._total_dropped / self._total_enqueued 
                if self._total_enqueued > 0 else 0.0
            )
            return RingBufferStats(
                capacity=self._capacity,
                size=size,
                total_enqueued=self._total_enqueued,
                total_dropped=self._total_dropped,
                drop_rate=drop_rate,
            )
```

### 2. WAL with Checksum (신규 구현) - `wal.py`

**리뷰어 조언:**
> "WAL 파일 checksum - 파일 손상 감지용"

```python
"""
Write-Ahead Log with CRC32 Checksum.

데이터 무결성 보장:
1. 메모리에 먼저 기록 전 WAL 작성
2. 각 엔트리에 CRC32 체크섬
3. 복구 시 체크섬 검증

최소 의존성: 표준 라이브러리만 사용 (struct, json, zlib)
"""

import hashlib
import json
import os
import struct
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Iterator
from threading import Lock


@dataclass
class WALEntry:
    """WAL 엔트리"""
    sequence: int
    timestamp: float
    data: dict
    checksum: str


class WriteAheadLog:
    """Write-Ahead Log with CRC32 Checksum."""
    
    MAGIC = b'AWAL'  # Audit WAL
    VERSION = 1
    
    def __init__(
        self,
        wal_dir: str = "/var/log/audit/wal",
        max_file_size_mb: int = 100,
        sync_on_write: bool = True,
    ):
        self._wal_dir = Path(wal_dir)
        self._max_file_size = max_file_size_mb * 1024 * 1024
        self._sync_on_write = sync_on_write
        self._current_file: Optional[Path] = None
        self._current_handle = None
        self._sequence = 0
        self._lock = Lock()
        
        self._wal_dir.mkdir(parents=True, exist_ok=True)
        self._init_or_recover()
    
    def _compute_checksum(self, data: bytes) -> str:
        """CRC32 체크섬 계산"""
        crc = zlib.crc32(data) & 0xffffffff
        return f"{crc:08x}"
    
    def write(self, data: dict) -> int:
        """WAL에 기록. Returns sequence number."""
        with self._lock:
            self._sequence += 1
            
            entry = {
                "seq": self._sequence,
                "ts": time.time(),
                "data": data,
            }
            entry_bytes = json.dumps(entry, separators=(',', ':')).encode('utf-8')
            checksum = self._compute_checksum(entry_bytes)
            
            # Format: [4-byte length][checksum:8][entry_bytes]
            record = struct.pack('>I', len(entry_bytes)) + \
                     checksum.encode('ascii') + \
                     entry_bytes
            
            self._ensure_file_open()
            self._current_handle.write(record)
            
            if self._sync_on_write:
                self._current_handle.flush()
                os.fsync(self._current_handle.fileno())
            
            if self._current_handle.tell() > self._max_file_size:
                self._rotate_file()
            
            return self._sequence
    
    def recover_unprocessed(self, last_processed_seq: int) -> List[WALEntry]:
        """마지막 처리된 시퀀스 이후 엔트리 복구"""
        entries = []
        for wal_file in sorted(self._wal_dir.glob("*.wal")):
            for entry in self._read_wal_file(wal_file):
                if entry.sequence > last_processed_seq:
                    entries.append(entry)
        return entries
```

### 3. Self-Audit Logger (신규 구현) - `self_audit.py`

**리뷰어 조언:**
> "Self-Audit: 감사 시스템 자체가 언제 실패했는지도 로깅되어야 함"

```python
"""
Self-Audit Logger.

감사 시스템 자체의 상태를 기록.

원칙:
1. 감사 시스템 실패도 기록되어야 함
2. 최소 의존성: 표준 stderr/syslog만 사용
3. 순환 의존 방지: 다른 audit 모듈과 독립
"""

import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Dict, Any


class SelfAuditEvent(Enum):
    """감사 시스템 자체 이벤트 유형"""
    STARTUP = "startup"
    SHUTDOWN = "shutdown"
    WAL_WRITE_FAILED = "wal_write_failed"
    PRIMARY_STORE_FAILED = "primary_store_failed"
    FALLBACK_ACTIVATED = "fallback_activated"
    FALLBACK_FAILED = "fallback_failed"
    SYSLOG_ACTIVATED = "syslog_activated"
    CIRCUIT_OPENED = "circuit_opened"
    CIRCUIT_CLOSED = "circuit_closed"
    BUFFER_OVERFLOW = "buffer_overflow"
    CHECKSUM_MISMATCH = "checksum_mismatch"
    HEARTBEAT_MISSED = "heartbeat_missed"


class SelfAuditLogger:
    """감사 시스템 자체의 상태를 기록."""
    
    _instance: Optional["SelfAuditLogger"] = None
    
    def __init__(self):
        self._logger = logging.getLogger("audit.self")
        self._stats = {"total": 0, "failures": 0}
        
    @classmethod
    def get_instance(cls) -> "SelfAuditLogger":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def log(
        self,
        event_type: SelfAuditEvent,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Self-Audit 이벤트 기록. 항상 성공해야 함."""
        try:
            self._stats["total"] += 1
            
            log_entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": "SELF_AUDIT",
                "event": event_type.value,
                "message": message,
            }
            
            self._logger.warning(f"[SELF-AUDIT] {event_type.value}: {message}")
            print(f"[SELF-AUDIT] {event_type.value}: {message}", file=sys.stderr)
                
        except Exception:
            pass  # Self-audit 실패는 조용히 넘어감 (무한 루프 방지)


def self_audit() -> SelfAuditLogger:
    return SelfAuditLogger.get_instance()
```

### 4. Audit Watchdog (신규 구현) - `watchdog.py`

**리뷰어 조언:**
> "External Heartbeat (Dead Man's Switch) - 외부 모니터링 시스템이 heartbeat를 감시"

```python
"""
Audit Watchdog - Dead Man's Switch Pattern.

감사 시스템 생존 확인:
- 주기적으로 heartbeat 전송
- 외부 모니터링 시스템이 heartbeat 감시
- heartbeat 누락 시 알림
"""

import threading
import time
import urllib.request
import urllib.error
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional


@dataclass
class WatchdogConfig:
    """Watchdog 설정"""
    heartbeat_interval_seconds: float = 30.0
    missed_heartbeat_threshold: int = 3
    heartbeat_endpoint: Optional[str] = None


class AuditWatchdog:
    """Dead Man's Switch 패턴 Watchdog."""
    
    def __init__(
        self,
        config: Optional[WatchdogConfig] = None,
        on_alive: Optional[Callable[[], None]] = None,
        on_dead: Optional[Callable[[int], None]] = None,
    ):
        self._config = config or WatchdogConfig()
        self._on_alive = on_alive
        self._on_dead = on_dead
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_heartbeat: Optional[datetime] = None
        self._missed_count = 0
    
    def start(self) -> None:
        """Watchdog 시작"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._heartbeat_loop,
            daemon=True,
            name="AuditWatchdog",
        )
        self._thread.start()
    
    def stop(self) -> None:
        """Watchdog 중지"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5.0)
    
    def pet(self) -> None:
        """수동 heartbeat (정상 작동 확인)"""
        self._last_heartbeat = datetime.now(timezone.utc)
        self._missed_count = 0
```

---

## 🔗 통합 구현: Resilient Continuous Audit Recorder

기존 `ContinuousAuditRecorder`에 resilience.py의 기능을 통합합니다.

```python
# continuous_audit.py 수정 (기존 코드에 추가)

from .resilience import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerRegistry,
    SyslogFallback,
    AuditMetrics,
)
from .ring_buffer import RingBuffer, BackpressureStrategy
from .wal import WriteAheadLog
from .self_audit import self_audit, SelfAuditEvent
from .watchdog import AuditWatchdog, WatchdogConfig


class ResilientContinuousAuditRecorder(ContinuousAuditRecorder):
    """
    장애 허용 연속 감사 기록기 (기존 코드 확장).
    
    추가된 기능:
    1. RingBuffer: 비침투 Shadow Logging
    2. CircuitBreaker 연결: resilience.py 재사용
    3. WAL: 데이터 무결성
    4. Syslog 연결: 최후의 수단
    5. Self-Audit: 자체 상태 기록
    6. Watchdog: Dead Man's Switch
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # 기존 resilience.py 구성요소 연결
        self._cb_registry = CircuitBreakerRegistry.get_instance()
        self._cb = self._cb_registry.get_or_create(
            "audit_primary",
            CircuitBreakerConfig(failure_threshold=3, timeout_seconds=30.0)
        )
        self._syslog = SyslogFallback.get_instance()
        self._metrics = AuditMetrics.get_instance()
        
        # 신규 구성요소
        self._buffer = RingBuffer(capacity=10000)
        self._wal = WriteAheadLog() if kwargs.get('enable_wal', True) else None
        self._watchdog = AuditWatchdog()
        
        self_audit().log(SelfAuditEvent.STARTUP, "Resilient Audit started")
        self._watchdog.start()
```

---

## 📊 구현 우선순위 로드맵

### Phase 1: Core Resilience (1-2일) - 총 12h

| 순서 | 항목 | 작업 | 예상 시간 |
|------|------|------|----------|
| 1 | RingBuffer | 신규 구현 | 2h |
| 2 | Self-Audit | 신규 구현 | 2h |
| 3 | 통합 | ContinuousAuditRecorder + resilience.py 연결 | 4h |
| 4 | 테스트 | Unit/Integration 테스트 | 4h |

### Phase 2: Data Integrity (1일) - 총 8h

| 순서 | 항목 | 작업 | 예상 시간 |
|------|------|------|----------|
| 1 | WAL | 신규 구현 (Checksum 포함) | 4h |
| 2 | 복구 로직 | 미처리 엔트리 재처리 | 2h |
| 3 | 테스트 | WAL 무결성 테스트 | 2h |

### Phase 3: External Monitoring (0.5일) - 총 4h

| 순서 | 항목 | 작업 | 예상 시간 |
|------|------|------|----------|
| 1 | Watchdog | 신규 구현 | 2h |
| 2 | Dead Man's Switch | 외부 연동 설정 | 2h |

**총 예상 공수: 24h (3일)**

---

## ✅ 완료 체크리스트

### 비침투성 원칙 준수

- [ ] `record()` 메서드 논블로킹
- [ ] RingBuffer DROP_OLDEST 전략 사용
- [ ] 백그라운드 스레드 데몬 모드
- [ ] 모든 외부 호출 타임아웃 설정

### 최소 의존성 원칙 준수

- [ ] WAL: 표준 라이브러리만 사용 (struct, json, zlib)
- [ ] Self-Audit: stderr/logging만 사용
- [ ] Watchdog: urllib만 사용 (requests 불필요)
- [ ] RingBuffer: collections.deque 사용

### 기존 코드 재사용

- [ ] CircuitBreaker: resilience.py 재사용
- [ ] SyslogFallback: resilience.py 재사용
- [ ] AuditMetrics: resilience.py 재사용
- [ ] Watchdog 패턴: pool_watchdog.py 참조

### 장애 허용 체인 구현

```
Primary (PostgreSQL)
    ↓ 실패
Fallback (Local File)
    ↓ 실패
Syslog (OS-level)
    ↓ 실패
stderr (최후)
```

---

## 🔧 환경 변수 설정

```bash
# .env
AUDIT_ENABLED=true
AUDIT_BUFFER_CAPACITY=10000
AUDIT_WAL_ENABLED=true
AUDIT_WAL_DIR=/var/log/audit/wal
AUDIT_HEARTBEAT_URL=https://deadmansswitch.io/ping/xxxx
AUDIT_CIRCUIT_BREAKER_THRESHOLD=3
AUDIT_CIRCUIT_BREAKER_TIMEOUT=30
```

---

## 📚 기존 코드 참조 위치

| 파일 | 경로 | 재사용 대상 |
|------|------|------------|
| resilience.py | `packages/selfhealing-python/src/selfhealing/audit/resilience.py` | CircuitBreaker, SyslogFallback, AuditMetrics, DegradedModeManager |
| async_logger.py | `load_tests/utils/selfhealing/async_logger.py` | AsyncHealingLogger (참조용) |
| pool_watchdog.py | `packages/selfhealing-python/src/selfhealing/core/pool_watchdog.py` | Watchdog 패턴 참조 |
| rate_limit.py | `shopping/api/django/rate_limit.py` | Rate Limiter (Log Storm 방지) |
| stage35_cache_poison.py | `load_tests/scenarios/security/stage35_cache_poison.py` | CRC32 Checksum 참조 |

---

*문서 버전: 2.0*  
*작성일: 2025-01-XX*  
*기반: 리뷰어 피드백 + 코드베이스 분석*
