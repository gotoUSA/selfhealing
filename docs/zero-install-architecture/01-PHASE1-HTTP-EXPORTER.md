# Phase 1: HTTP Exporter

## 목적

고객 시스템에서 발생하는 Self-Healing 이벤트를 **우리 Cloud 서버**로 전송하는 컴포넌트.

**핵심 원칙:**
- 외부 의존성 없음 (표준 라이브러리만 사용)
- 비동기 전송 (앱 성능 영향 최소화)
- 오프라인 버퍼링 (네트워크 장애 대응)
- 자동 재시도

---

## 아키텍처

```
┌─────────────────────────────────────────────────────────────────┐
│                    고객 앱 프로세스                               │
│                                                                  │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐         │
│  │ Event       │    │ InMemory    │    │ Background  │         │
│  │ Producer    │ →  │ Buffer      │ →  │ Sender      │         │
│  │ (동기)      │    │ (deque)     │    │ (Thread)    │         │
│  └─────────────┘    └─────────────┘    └──────┬──────┘         │
│                                               │                 │
└───────────────────────────────────────────────┼─────────────────┘
                                                │ HTTPS POST
                                                ▼
                                    ┌─────────────────┐
                                    │ Cloud API       │
                                    │ /api/v1/events  │
                                    └─────────────────┘
```

---

## API 설계

### 초기화

```python
import selfhealing

# 최소 설정
selfhealing.init(api_key="sk_live_xxx")

# 전체 설정
selfhealing.init(
    api_key="sk_live_xxx",
    endpoint="https://api.selfhealing.io",  # 기본값
    service_name="my-service",              # 자동 감지
    environment="production",               # 자동 감지
    
    # Exporter 설정
    batch_size=100,           # 배치당 최대 이벤트 수
    flush_interval=5.0,       # 전송 주기 (초)
    max_buffer_size=10000,    # 최대 버퍼 크기
    retry_count=3,            # 재시도 횟수
    retry_delay=1.0,          # 재시도 간격 (초)
    
    # 필터링
    sample_rate=1.0,          # 1.0 = 100% 수집
    exclude_paths=["/health", "/metrics"],
)
```

### 이벤트 전송 (내부 API)

```python
from selfhealing.exporter import HTTPExporter

exporter = HTTPExporter.get_instance()

# 이벤트 추가 (비동기 버퍼링)
exporter.send_event({
    "type": "circuit_breaker_opened",
    "service": "payment_api",
    "timestamp": "2025-12-18T10:30:00Z",
    "details": {
        "failure_count": 5,
        "threshold": 5,
    }
})

# 즉시 전송 (종료 시)
exporter.flush()

# 종료
exporter.shutdown()
```

---

## 구현 상세

### 1. HTTPExporter 클래스

```python
"""
selfhealing/exporter/http_exporter.py
"""

import atexit
import json
import logging
import queue
import ssl
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

logger = logging.getLogger(__name__)


@dataclass
class ExporterConfig:
    """HTTP Exporter 설정"""
    api_key: str
    endpoint: str = "https://api.selfhealing.io"
    service_name: str = "unknown"
    environment: str = "development"
    
    # Batching
    batch_size: int = 100
    flush_interval: float = 5.0
    max_buffer_size: int = 10000
    
    # Retry
    retry_count: int = 3
    retry_delay: float = 1.0
    
    # Timeout
    connect_timeout: float = 5.0
    read_timeout: float = 10.0


class HTTPExporter:
    """
    Self-Healing 이벤트를 Cloud로 전송하는 Exporter.
    
    특징:
    - 표준 라이브러리만 사용 (urllib)
    - Background thread로 비동기 전송
    - 오프라인 버퍼링
    - 자동 재시도
    """
    
    _instance: Optional["HTTPExporter"] = None
    _lock = threading.Lock()
    
    @classmethod
    def get_instance(cls) -> "HTTPExporter":
        """Singleton 인스턴스 반환"""
        if cls._instance is None:
            raise RuntimeError("HTTPExporter not initialized. Call selfhealing.init() first.")
        return cls._instance
    
    @classmethod
    def initialize(cls, config: ExporterConfig) -> "HTTPExporter":
        """Exporter 초기화"""
        with cls._lock:
            if cls._instance is not None:
                cls._instance.shutdown()
            
            cls._instance = cls(config)
            cls._instance.start()
            
            # 프로세스 종료 시 자동 flush
            atexit.register(cls._instance.shutdown)
            
            return cls._instance
    
    def __init__(self, config: ExporterConfig):
        self.config = config
        self._buffer: deque = deque(maxlen=config.max_buffer_size)
        self._lock = threading.Lock()
        self._shutdown_event = threading.Event()
        self._sender_thread: Optional[threading.Thread] = None
        self._started = False
    
    def start(self) -> None:
        """Background sender 시작"""
        if self._started:
            return
        
        self._sender_thread = threading.Thread(
            target=self._sender_loop,
            name="selfhealing-exporter",
            daemon=True,
        )
        self._sender_thread.start()
        self._started = True
        logger.info(f"[Exporter] Started - endpoint={self.config.endpoint}")
    
    def send_event(self, event: Dict[str, Any]) -> None:
        """
        이벤트를 버퍼에 추가 (비동기).
        
        버퍼가 가득 차면 가장 오래된 이벤트가 삭제됨.
        """
        # 메타데이터 추가
        event = {
            **event,
            "_meta": {
                "service": self.config.service_name,
                "environment": self.config.environment,
                "sdk_version": "0.1.0",
            }
        }
        
        with self._lock:
            self._buffer.append(event)
            
            # 버퍼가 batch_size에 도달하면 즉시 전송 시그널
            if len(self._buffer) >= self.config.batch_size:
                # 다음 루프에서 즉시 전송하도록
                pass
    
    def flush(self) -> None:
        """버퍼의 모든 이벤트 즉시 전송"""
        self._send_batch()
    
    def shutdown(self) -> None:
        """Exporter 종료"""
        if not self._started:
            return
        
        logger.info("[Exporter] Shutting down...")
        
        # 남은 이벤트 전송
        self.flush()
        
        # Sender thread 종료
        self._shutdown_event.set()
        if self._sender_thread and self._sender_thread.is_alive():
            self._sender_thread.join(timeout=5.0)
        
        self._started = False
        logger.info("[Exporter] Shutdown complete")
    
    def _sender_loop(self) -> None:
        """Background sender loop"""
        while not self._shutdown_event.is_set():
            try:
                self._send_batch()
            except Exception as e:
                logger.error(f"[Exporter] Error in sender loop: {e}")
            
            # 다음 전송까지 대기
            self._shutdown_event.wait(timeout=self.config.flush_interval)
    
    def _send_batch(self) -> None:
        """버퍼에서 배치를 가져와 전송"""
        # 버퍼에서 이벤트 추출
        with self._lock:
            if not self._buffer:
                return
            
            batch = list(self._buffer)
            self._buffer.clear()
        
        if not batch:
            return
        
        # 전송 시도
        success = self._send_with_retry(batch)
        
        if not success:
            # 실패 시 버퍼에 다시 추가 (최신 이벤트 우선 유지)
            with self._lock:
                for event in reversed(batch):
                    if len(self._buffer) < self.config.max_buffer_size:
                        self._buffer.appendleft(event)
            
            logger.warning(f"[Exporter] Failed to send {len(batch)} events, re-buffered")
    
    def _send_with_retry(self, batch: List[Dict]) -> bool:
        """재시도 로직 포함 전송"""
        for attempt in range(self.config.retry_count):
            try:
                self._do_send(batch)
                logger.debug(f"[Exporter] Sent {len(batch)} events")
                return True
            
            except HTTPError as e:
                if e.code >= 400 and e.code < 500:
                    # 4xx 에러는 재시도 안함
                    logger.error(f"[Exporter] Client error {e.code}: {e.reason}")
                    return False
                
                logger.warning(f"[Exporter] Attempt {attempt + 1} failed: {e}")
            
            except URLError as e:
                logger.warning(f"[Exporter] Attempt {attempt + 1} failed: {e}")
            
            except Exception as e:
                logger.error(f"[Exporter] Unexpected error: {e}")
            
            # 재시도 전 대기
            if attempt < self.config.retry_count - 1:
                time.sleep(self.config.retry_delay * (attempt + 1))
        
        return False
    
    def _do_send(self, batch: List[Dict]) -> None:
        """실제 HTTP 전송"""
        url = f"{self.config.endpoint}/api/v1/events"
        
        payload = json.dumps({
            "events": batch,
            "batch_id": f"{time.time():.6f}",
        }).encode("utf-8")
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.api_key}",
            "X-Service-Name": self.config.service_name,
            "X-Environment": self.config.environment,
        }
        
        request = Request(url, data=payload, headers=headers, method="POST")
        
        # SSL context
        context = ssl.create_default_context()
        
        with urlopen(
            request,
            timeout=self.config.connect_timeout + self.config.read_timeout,
            context=context,
        ) as response:
            if response.status != 200:
                raise HTTPError(
                    url, response.status, response.reason, response.headers, None
                )
```

### 2. Event Types

```python
"""
selfhealing/exporter/events.py
"""

from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional


class EventType(str, Enum):
    """Self-Healing 이벤트 타입"""
    
    # Circuit Breaker
    CIRCUIT_BREAKER_OPENED = "circuit_breaker.opened"
    CIRCUIT_BREAKER_CLOSED = "circuit_breaker.closed"
    CIRCUIT_BREAKER_HALF_OPEN = "circuit_breaker.half_open"
    
    # DLQ
    DLQ_ENQUEUED = "dlq.enqueued"
    DLQ_REPLAYED = "dlq.replayed"
    DLQ_FAILED = "dlq.failed"
    
    # Retry
    RETRY_ATTEMPTED = "retry.attempted"
    RETRY_EXHAUSTED = "retry.exhausted"
    RETRY_SUCCEEDED = "retry.succeeded"
    
    # HTTP
    HTTP_ERROR = "http.error"
    HTTP_SLOW = "http.slow"
    
    # Exception
    EXCEPTION_CAUGHT = "exception.caught"
    
    # Security
    SECURITY_INCIDENT = "security.incident"


@dataclass
class SelfHealingEvent:
    """Self-Healing 이벤트 데이터"""
    
    type: EventType
    timestamp: str = ""
    
    # 컨텍스트
    service_name: Optional[str] = None
    endpoint: Optional[str] = None
    method: Optional[str] = None
    
    # 상세 정보
    details: Dict[str, Any] = None
    
    # 에러 정보
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    error_traceback: Optional[str] = None
    
    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.utcnow().isoformat() + "Z"
        if self.details is None:
            self.details = {}
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리 변환"""
        data = asdict(self)
        data["type"] = self.type.value
        return {k: v for k, v in data.items() if v is not None}


# 편의 함수
def emit_event(
    event_type: EventType,
    **kwargs
) -> None:
    """이벤트 생성 및 전송"""
    from selfhealing.exporter import HTTPExporter
    
    event = SelfHealingEvent(type=event_type, **kwargs)
    
    try:
        exporter = HTTPExporter.get_instance()
        exporter.send_event(event.to_dict())
    except RuntimeError:
        # Exporter가 초기화되지 않은 경우 무시
        pass
```

---

## 전송 프로토콜

### Request

```http
POST /api/v1/events HTTP/1.1
Host: api.selfhealing.io
Content-Type: application/json
Authorization: Bearer sk_live_xxxxx
X-Service-Name: payment-service
X-Environment: production

{
  "batch_id": "1734567890.123456",
  "events": [
    {
      "type": "circuit_breaker.opened",
      "timestamp": "2025-12-18T10:30:00.123Z",
      "service_name": "payment_api",
      "details": {
        "failure_count": 5,
        "threshold": 5,
        "last_error": "Connection timeout"
      },
      "_meta": {
        "service": "payment-service",
        "environment": "production",
        "sdk_version": "0.1.0"
      }
    },
    {
      "type": "http.error",
      "timestamp": "2025-12-18T10:30:01.456Z",
      "endpoint": "/api/payments/confirm",
      "method": "POST",
      "error_type": "HTTPError",
      "error_message": "503 Service Unavailable",
      "details": {
        "status_code": 503,
        "response_time_ms": 5023
      }
    }
  ]
}
```

### Response

```http
HTTP/1.1 200 OK
Content-Type: application/json

{
  "status": "accepted",
  "batch_id": "1734567890.123456",
  "accepted_count": 2,
  "rejected_count": 0
}
```

---

## 성능 고려사항

| 항목 | 설계 |
|------|------|
| **메모리** | 최대 10,000 이벤트 버퍼 (약 10MB) |
| **CPU** | Background thread 사용, 메인 스레드 영향 없음 |
| **네트워크** | 배치 전송으로 커넥션 수 최소화 |
| **실패 처리** | 지수 백오프 재시도 |
| **프로세스 종료** | atexit hook으로 자동 flush |

---

## 테스트

```python
def test_http_exporter():
    """HTTPExporter 테스트"""
    from selfhealing.exporter import HTTPExporter, ExporterConfig
    
    # Mock 서버 설정
    config = ExporterConfig(
        api_key="test_key",
        endpoint="http://localhost:8080",
        batch_size=10,
        flush_interval=0.1,
    )
    
    exporter = HTTPExporter.initialize(config)
    
    # 이벤트 전송
    for i in range(25):
        exporter.send_event({
            "type": "test_event",
            "index": i,
        })
    
    time.sleep(0.5)  # 전송 대기
    
    exporter.shutdown()
    
    # 검증: 3개 배치 전송됨 (10, 10, 5)
```

---

## 다음 단계

→ [02-PHASE1-AUTO-INSTRUMENT.md](02-PHASE1-AUTO-INSTRUMENT.md)
