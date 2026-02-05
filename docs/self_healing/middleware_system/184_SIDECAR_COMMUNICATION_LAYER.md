# 184. 사이드카 통신 레이어 설계

> **문서 버전**: 2.1.0
> **최종 수정일**: 2026-02-06
> **작성 근거**: `selfhealing` 패키지 인터페이스 분석
> **구현 상태**: ✅ 완료

## 구현 현황

| 컴포넌트 | 파일 | 상태 |
|----------|------|------|
| JSON-RPC 프로토콜 | `adapters/ipc/protocol/json_rpc.py` | ✅ 구현 완료 |
| UDS 서버 | `adapters/ipc/uds_server.py` | ✅ 구현 완료 |
| UDS 클라이언트 | `adapters/ipc/uds_client.py` | ✅ 구현 완료 |
| gRPC 서버 | `adapters/ipc/grpc_server.py` | ✅ 구현 완료 |
| 인증 | `adapters/ipc/auth.py` | ✅ 구현 완료 |
| CB 상태 캐시 | `adapters/ipc/cb_state_cache.py` | ✅ 구현 완료 |
| CB 상태 스냅샷 | `adapters/ipc/cb_state_snapshot.py` | ✅ 구현 완료 |
| 이벤트 스트림 프록시 | `adapters/ipc/event_stream_proxy.py` | ✅ 구현 완료 |
| 사이드카 메트릭 | `adapters/ipc/sidecar_metrics.py` | ✅ 구현 완료 |
| IPC 헬스 프로브 | `adapters/ipc/sidecar_ipc_probe.py` | ✅ 구현 완료 |

### 단위 테스트 (217 passed)

```
tests/unit/adapters/ipc/
├── test_json_rpc.py           # 32 tests
├── test_auth.py               # 28 tests
├── test_cb_state_cache.py     # 21 tests
├── test_cb_state_snapshot.py  # 21 tests
├── test_event_stream_proxy.py # 18 tests
├── test_grpc_server.py        # 25 tests
├── test_sidecar_ipc_probe.py  # 19 tests
├── test_sidecar_metrics.py    # 26 tests
├── test_uds_client.py         # 16 tests
└── test_uds_server.py         # 12 tests
```

## 목차

1. [개요](#1-개요)
2. [통신 방식 비교](#2-통신-방식-비교)
3. [현재 인터페이스와 매핑](#3-현재-인터페이스와-매핑)
4. [어댑터 계층 구조](#4-어댑터-계층-구조)
5. [메시지 프로토콜](#5-메시지-프로토콜)
6. [클라이언트 SDK 가이드](#6-클라이언트-sdk-가이드)
7. [보안 고려사항](#7-보안-고려사항)
8. [배치 처리 (Batching) 지원](#8-배치-처리-batching-지원)
9. [이벤트 스트리밍 (EventStreamProxy)](#9-이벤트-스트리밍-eventstreamproxy)
10. [영속성 버퍼 API (BufferStore)](#10-영속성-버퍼-api-bufferstore)
11. [분산 트레이싱 (W3C Trace Context)](#11-분산-트레이싱-w3c-trace-context)
12. [사이드카 전용 메트릭](#12-사이드카-전용-메트릭)
13. [메타-와치독 통합 (SidecarIPCProbe)](#13-메타-와치독-통합-sidecaripcprobe)
14. [서킷 브레이커 상태 캐시 (CBStateCache)](#14-서킷-브레이커-상태-캐시-cbstatecache)
15. [사이드카 인증 (Static Bearer Token)](#15-사이드카-인증-static-bearer-token)
16. [Service Mesh (Istio) 배포 가이드](#16-service-mesh-istio-배포-가이드)
17. [사이드카 장애 시 폴백 정책](#17-사이드카-장애-시-폴백-정책)
18. [Shared Memory 기반 CB 상태 공유 (CBStateSnapshot)](#18-shared-memory-기반-cb-상태-공유-cbstatesnapshot)
19. [SDK 코드 생성 자동화](#19-sdk-코드-생성-자동화)
20. [관련 문서](#20-관련-문서)

---

## 1. 개요

본 문서는 타언어 애플리케이션이 Self-Healing 시스템을 사용할 수 있도록 하는 **통신 레이어**를 설계합니다.

---

## 2. 통신 방식 비교

### 2.1 UDS (Unix Domain Socket)

```
┌────────────────────────────────────────────────────────────────┐
│                     Same Host / Pod                             │
│                                                                 │
│  ┌─────────────┐                      ┌─────────────────────┐  │
│  │  Go App     │                      │  Sidecar            │  │
│  │             │                      │                     │  │
│  │  ┌───────┐  │   /tmp/selfhealing   │  ┌───────────────┐  │  │
│  │  │ UDS   │  │        .sock         │  │ UDS Server    │  │  │
│  │  │Client │══╬══════════════════════╬══│               │  │  │
│  │  └───────┘  │     (No Network      │  │ ┌───────────┐ │  │  │
│  │             │      Stack)          │  │ │selfhealing│ │  │  │
│  │             │                      │  │ │  core     │ │  │  │
│  └─────────────┘                      │  │ └───────────┘ │  │  │
│                                       │  └───────────────┘  │  │
│                                       └─────────────────────┘  │
└────────────────────────────────────────────────────────────────┘

장점:
- 네트워크 스택 우회 → 50% 이상 오버헤드 절감
- 커널 내부 파이프라인으로 데이터 전송
- 파일 시스템 권한으로 보안 (소켓 파일 접근 제어)

단점:
- 동일 머신에서만 사용 가능
- Windows는 네이티브 지원 없음 (WSL 필요)
```

### 2.2 gRPC (localhost)

```
┌────────────────────────────────────────────────────────────────┐
│                     Same Host / Pod                             │
│                                                                 │
│  ┌─────────────┐                      ┌─────────────────────┐  │
│  │  Java App   │                      │  Sidecar            │  │
│  │             │                      │                     │  │
│  │  ┌───────┐  │   localhost:50051    │  ┌───────────────┐  │  │
│  │  │ gRPC  │  │      (HTTP/2)        │  │ gRPC Server   │  │  │
│  │  │Stub   │──┼──────────────────────┼──│ (Protobuf)    │  │  │
│  │  └───────┘  │                      │  │ ┌───────────┐ │  │  │
│  │             │                      │  │ │selfhealing│ │  │  │
│  │             │                      │  │ │  core     │ │  │  │
│  └─────────────┘                      │  │ └───────────┘ │  │  │
│                                       │  └───────────────┘  │  │
└────────────────────────────────────────────────────────────────┘

장점:
- 언어 독립적 (모든 주요 언어 지원)
- Protobuf로 효율적 직렬화
- 양방향 스트리밍 지원
- 강타입 API 정의

단점:
- UDS보다 약간 높은 오버헤드
- TLS 설정 복잡도
```

---

## 3. 현재 인터페이스와 매핑

### 3.1 CircuitBreakerService API

**코드 근거** (`services/circuit_breaker/service.py`):
```python
class CircuitBreakerService:
    def should_allow(self, service_name: str) -> bool: ...
    def get_state(self, service_name: str) -> str: ...
    def force_open(self, service_name: str, reason: str, controlled_by: str) -> CircuitBreakerResult: ...
    def force_close(self, service_name: str, reason: str, controlled_by: str, trigger_replay: bool) -> CircuitBreakerResult: ...
```

**gRPC 매핑** (제안):
```protobuf
service CircuitBreakerService {
  rpc ShouldAllow(ShouldAllowRequest) returns (ShouldAllowResponse);
  rpc GetState(GetStateRequest) returns (GetStateResponse);
  rpc ForceOpen(ForceOpenRequest) returns (CircuitBreakerResult);
  rpc ForceClose(ForceCloseRequest) returns (CircuitBreakerResult);
}

message ShouldAllowRequest {
  string service_name = 1;
}

message ShouldAllowResponse {
  bool allowed = 1;
  string state = 2;  // "closed", "open", "half_open"
}
```

### 3.2 DLQService API

**코드 근거** (`services/dlq/base.py`):
```python
class DLQServiceBase:
    def is_enabled(self) -> bool: ...

# store_operations.py
class StoreOperationsMixin:
    def store_with_snapshot(
        self,
        domain: str,
        failure_type: str,
        error_code: str,
        error_message: str,
        snapshot_data: dict,
        request_data: dict | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        user_id: int | None = None,
        max_retries: int | None = None,
        metadata: dict | None = None,
        request: Any = None,
    ) -> DLQEntryResult: ...
```

**gRPC 매핑** (제안):
```protobuf
service DLQService {
  rpc Store(StoreRequest) returns (StoreResponse);
  rpc Replay(ReplayRequest) returns (ReplayResponse);
  rpc GetEntry(GetEntryRequest) returns (DLQEntry);
}

message StoreRequest {
  string domain = 1;
  string failure_type = 2;
  string error_code = 3;
  string error_message = 4;
  bytes snapshot_data = 5;   // JSON serialized
  bytes request_data = 6;    // JSON serialized
  optional string entity_type = 7;
  optional string entity_id = 8;
  optional int64 user_id = 9;
  optional int32 max_retries = 10;
}
```

### 3.3 LearningService API

**코드 근거** (`services/learning/service.py`):
```python
class LearningService:
    def record_success(self, pattern_type: str, context: dict) -> None: ...
    def record_failure(self, pattern_type: str, context: dict, error: str) -> None: ...
    def get_suggestions(self, limit: int = 10) -> list[Suggestion]: ...
    def analyze_patterns(self) -> list[LearningPattern]: ...
```

**gRPC 매핑** (제안):
```protobuf
service LearningService {
  rpc RecordSuccess(RecordRequest) returns (Empty);
  rpc RecordFailure(RecordFailureRequest) returns (Empty);
  rpc GetSuggestions(GetSuggestionsRequest) returns (SuggestionList);
  rpc AnalyzePatterns(Empty) returns (PatternList);
}
```

---

## 4. 어댑터 계층 구조

### 4.1 제안 아키텍처

```
selfhealing/adapters/
├── ipc/                          # IPC 통신 어댑터 (신규)
│   ├── __init__.py
│   ├── uds_server.py             # UDS 서버
│   ├── uds_client.py             # UDS 클라이언트 (테스트용)
│   ├── grpc_server.py            # gRPC 서버
│   └── protocol/
│       ├── selfhealing.proto     # Protobuf 정의
│       └── selfhealing_pb2.py    # 생성된 Python 코드
│
├── cache/                        # 기존 캐시 어댑터
│   ├── redis_adapter.py
│   └── memory_adapter.py
│
└── kafka/                        # 기존 Kafka 어댑터
    ├── producer.py
    └── consumer.py
```

### 4.2 UDS 서버 설계

**ProviderRegistry 확장 방식** (`factory.py` 패턴 활용):
```python
# adapters/ipc/uds_server.py (신규 구현)
import socket
import json
from typing import Any

class UDSServer:
    """
    Unix Domain Socket 기반 Self-Healing API 서버.

    기존 서비스를 감싸서 IPC로 노출:
    - CircuitBreakerService
    - DLQService
    - LearningService
    """

    SOCKET_PATH = "/tmp/selfhealing.sock"

    def __init__(self):
        # 기존 서비스 인스턴스 재사용
        from selfhealing.services import (
            get_circuit_breaker_service,
            get_dlq_service,
        )
        from selfhealing.services.learning import LearningService

        self.cb_service = get_circuit_breaker_service()
        self.dlq_service = get_dlq_service()
        self.learning_service = LearningService()

    def handle_request(self, request: dict) -> dict:
        """요청 라우팅 및 처리"""
        method = request.get("method")
        params = request.get("params", {})

        handlers = {
            "circuit_breaker.should_allow": self._cb_should_allow,
            "circuit_breaker.get_state": self._cb_get_state,
            "circuit_breaker.force_open": self._cb_force_open,
            "dlq.store": self._dlq_store,
            "learning.get_suggestions": self._learning_suggestions,
        }

        handler = handlers.get(method)
        if not handler:
            return {"error": f"Unknown method: {method}"}

        return handler(**params)
```

---

## 5. 메시지 프로토콜

### 5.1 JSON-RPC 스타일 (UDS용)

```json
// Request
{
  "jsonrpc": "2.0",
  "method": "circuit_breaker.should_allow",
  "params": {
    "service_name": "payment_gateway"
  },
  "id": "req-001"
}

// Response
{
  "jsonrpc": "2.0",
  "result": {
    "allowed": true,
    "state": "closed",
    "failure_count": 2,
    "success_count": 150
  },
  "id": "req-001"
}
```

### 5.2 에러 응답

```json
{
  "jsonrpc": "2.0",
  "error": {
    "code": -32601,
    "message": "Method not found",
    "data": {
      "method": "unknown.method"
    }
  },
  "id": "req-001"
}
```

---

## 6. 클라이언트 SDK 가이드

### 6.1 Go 클라이언트 (예시)

```go
package selfhealing

import (
    "encoding/json"
    "net"
)

type Client struct {
    conn net.Conn
}

func NewClient(socketPath string) (*Client, error) {
    conn, err := net.Dial("unix", socketPath)
    if err != nil {
        return nil, err
    }
    return &Client{conn: conn}, nil
}

func (c *Client) ShouldAllow(serviceName string) (bool, error) {
    request := map[string]interface{}{
        "jsonrpc": "2.0",
        "method":  "circuit_breaker.should_allow",
        "params":  map[string]string{"service_name": serviceName},
        "id":      "1",
    }

    // Send request
    encoder := json.NewEncoder(c.conn)
    if err := encoder.Encode(request); err != nil {
        return false, err
    }

    // Read response
    var response map[string]interface{}
    decoder := json.NewDecoder(c.conn)
    if err := decoder.Decode(&response); err != nil {
        return false, err
    }

    result := response["result"].(map[string]interface{})
    return result["allowed"].(bool), nil
}
```

### 6.2 Java 클라이언트 (예시)

```java
public class SelfHealingClient {
    private final ManagedChannel channel;
    private final CircuitBreakerServiceGrpc.CircuitBreakerServiceBlockingStub stub;

    public SelfHealingClient(String host, int port) {
        channel = ManagedChannelBuilder.forAddress(host, port)
            .usePlaintext()  // localhost only
            .build();
        stub = CircuitBreakerServiceGrpc.newBlockingStub(channel);
    }

    public boolean shouldAllow(String serviceName) {
        ShouldAllowRequest request = ShouldAllowRequest.newBuilder()
            .setServiceName(serviceName)
            .build();
        ShouldAllowResponse response = stub.shouldAllow(request);
        return response.getAllowed();
    }
}
```

---

## 7. 보안 고려사항

### 7.1 UDS 보안

```bash
# 소켓 파일 권한 설정
chmod 660 /tmp/selfhealing.sock
chown app:app /tmp/selfhealing.sock

# 동일 그룹의 프로세스만 접근 가능
```

### 7.2 gRPC mTLS (프로덕션)

```yaml
# sidecar-config.yaml
grpc:
  port: 50051
  tls:
    enabled: true
    cert_file: /etc/selfhealing/server.crt
    key_file: /etc/selfhealing/server.key
    ca_file: /etc/selfhealing/ca.crt
    client_auth: required
```

---

## 8. 배치 처리 (Batching) 지원

### 8.1 ShouldAllowBatch API

**코드 근거** (`185_SIDECAR_PERFORMANCE_ANALYSIS.md` §5.3):
```
Before: 10 services × 0.1ms = 1.0ms
After:  1 batch × 0.15ms = 0.15ms (85% 절감)
```

**gRPC 인터페이스 정의**:
```protobuf
service CircuitBreakerService {
  // 기존 단일 요청
  rpc ShouldAllow(ShouldAllowRequest) returns (ShouldAllowResponse);

  // 배치 요청 (신규)
  rpc ShouldAllowBatch(ShouldAllowBatchRequest) returns (ShouldAllowBatchResponse);
}

message ShouldAllowBatchRequest {
  repeated string service_names = 1;  // 최대 100개
}

message ShouldAllowBatchResponse {
  map<string, ServiceState> results = 1;
}

message ServiceState {
  bool allowed = 1;
  string state = 2;      // "closed", "open", "half_open"
  int32 failure_count = 3;
  int32 success_count = 4;
}
```

**UDS JSON-RPC 프로토콜**:
```json
// Request
{
  "jsonrpc": "2.0",
  "method": "circuit_breaker.should_allow_batch",
  "params": {
    "service_names": ["payment_gateway", "inventory_api", "notification_service"]
  },
  "id": "batch-001"
}

// Response
{
  "jsonrpc": "2.0",
  "result": {
    "payment_gateway": {"allowed": true, "state": "closed"},
    "inventory_api": {"allowed": false, "state": "open"},
    "notification_service": {"allowed": true, "state": "half_open"}
  },
  "id": "batch-001"
}
```

---

## 9. 이벤트 스트리밍 (EventStreamProxy)

### 9.1 개요

**코드 근거** (`services/event_bus.py`):
```python
class EventType(Enum):
    CIRCUIT_BREAKER_OPENED = "circuit_breaker_opened"
    CIRCUIT_BREAKER_CLOSED = "circuit_breaker_closed"
    CIRCUIT_BREAKER_HALF_OPENED = "circuit_breaker_half_opened"
    EMERGENCY_LEVEL_CHANGED = "emergency_level_changed"
```

타언어 앱이 CB 상태 변경 등 중요 이벤트를 **실시간 푸시**로 받을 수 있도록 합니다.

### 9.2 gRPC Server-Side Streaming

```protobuf
service EventService {
  // Server-side streaming: 사이드카 → 클라이언트
  rpc SubscribeEvents(EventSubscription) returns (stream SelfHealingEvent);
}

message EventSubscription {
  repeated string event_types = 1;  // 구독할 이벤트 타입
  // 예: ["circuit_breaker_opened", "emergency_level_changed"]
}

message SelfHealingEvent {
  string event_type = 1;
  string source = 2;
  string timestamp = 3;       // ISO8601
  bytes data = 4;             // JSON serialized
  string correlation_id = 5;
}
```

### 9.3 EventStreamProxy 구현 설계

```python
# adapters/ipc/event_stream_proxy.py (신규)
class EventStreamProxy:
    """
    SelfHealingEventBus를 gRPC 스트림으로 중계.

    코드 근거: services/event_bus.py - subscribe() 패턴
    """

    def __init__(self):
        from selfhealing.services.event_bus import get_event_bus, EventType
        self._bus = get_event_bus()
        self._streams: dict[str, queue.Queue] = {}

    def subscribe(self, event_types: list[str], stream_id: str):
        """이벤트 구독 및 큐 생성"""
        q = queue.Queue(maxsize=1000)
        self._streams[stream_id] = q

        for event_type_str in event_types:
            event_type = EventType(event_type_str)
            self._bus.subscribe(
                event_type,
                lambda e, q=q: q.put_nowait(e.to_dict())
            )

        return q

    def unsubscribe(self, stream_id: str):
        """스트림 정리"""
        if stream_id in self._streams:
            del self._streams[stream_id]
```

### 9.4 Go 클라이언트 예시

```go
func (c *Client) SubscribeEvents(ctx context.Context, eventTypes []string) error {
    stream, err := c.stub.SubscribeEvents(ctx, &EventSubscription{
        EventTypes: eventTypes,
    })
    if err != nil {
        return err
    }

    for {
        event, err := stream.Recv()
        if err == io.EOF {
            break
        }
        if err != nil {
            return err
        }

        // 이벤트 처리
        switch event.EventType {
        case "circuit_breaker_opened":
            log.Printf("CB Opened: %s", event.Data)
        }
    }
    return nil
}
```

---

## 10. 영속성 버퍼 API (BufferStore)

### 10.1 개요

**코드 근거** (`audit/persistence/disk_buffer.py`):
```python
class DiskPersistentBuffer:
    """LMDB 기반 Disk-Persistent Buffer. Pod 재시작에도 데이터 보존."""

    def put(self, entry: dict[str, Any]) -> bool: ...
    def flush_to(self, callback: Callable) -> int: ...
```

타언어 앱이 네트워크 장애 시 사이드카의 LMDB 버퍼에 직접 저장할 수 있도록 합니다.

### 10.2 gRPC 인터페이스

```protobuf
service BufferService {
  rpc Store(BufferStoreRequest) returns (BufferStoreResponse);
  rpc Flush(FlushRequest) returns (FlushResponse);
  rpc GetStats(Empty) returns (BufferStats);
}

message BufferStoreRequest {
  string entry_type = 1;      // "dlq", "audit", "metric"
  bytes data = 2;             // JSON serialized
  string dedup_key = 3;       // 중복 방지 키 (선택)
}

message BufferStoreResponse {
  bool success = 1;
  int64 sequence = 2;         // 저장된 시퀀스 번호
  string error = 3;
}

message BufferStats {
  int64 total_entries = 1;
  int64 pending_flush = 2;
  string state = 3;           // "active", "disk_full_failopen", "closed"
}
```

### 10.3 UDS JSON-RPC 프로토콜

```json
// Request
{
  "jsonrpc": "2.0",
  "method": "buffer.store",
  "params": {
    "entry_type": "dlq",
    "data": {"domain": "payment", "failure_type": "PG_TIMEOUT", "error_message": "..."},
    "dedup_key": "payment:order:12345"
  },
  "id": "buf-001"
}

// Response
{
  "jsonrpc": "2.0",
  "result": {
    "success": true,
    "sequence": 42
  },
  "id": "buf-001"
}
```

---

## 11. 분산 트레이싱 (W3C Trace Context)

### 11.1 개요

**코드 근거** (`services/event_bus.py`):
```python
@dataclass
class SelfHealingEvent:
    correlation_id: str | None = None  # TraceID 전파 가능
```

W3C Trace Context 표준(`traceparent` 헤더)을 준수하여 분산 트레이싱을 지원합니다.

### 11.2 메시지 헤더/바디 확장

**gRPC Metadata 전파**:
```protobuf
// 모든 Request 메시지에 trace_context 추가
message TraceContext {
  string traceparent = 1;     // W3C: "00-{trace_id}-{span_id}-{flags}"
  string tracestate = 2;      // 벤더 확장 (선택)
}

message ShouldAllowRequest {
  string service_name = 1;
  TraceContext trace_context = 2;  // 신규
}
```

**UDS JSON-RPC 확장**:
```json
{
  "jsonrpc": "2.0",
  "method": "circuit_breaker.should_allow",
  "params": {
    "service_name": "payment_gateway"
  },
  "metadata": {
    "traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
    "tracestate": "selfhealing=sidecar"
  },
  "id": "req-001"
}
```

### 11.3 SDK 구현 가이드

**Go 클라이언트**:
```go
import "go.opentelemetry.io/otel/propagation"

func (c *Client) ShouldAllowWithTrace(ctx context.Context, serviceName string) (bool, error) {
    // 현재 span에서 traceparent 추출
    carrier := propagation.MapCarrier{}
    propagation.TraceContext{}.Inject(ctx, carrier)

    request := &ShouldAllowRequest{
        ServiceName: serviceName,
        TraceContext: &TraceContext{
            Traceparent: carrier.Get("traceparent"),
            Tracestate:  carrier.Get("tracestate"),
        },
    }

    return c.stub.ShouldAllow(ctx, request)
}
```

**Java 클라이언트**:
```java
import io.opentelemetry.context.propagation.TextMapPropagator;

public boolean shouldAllowWithTrace(String serviceName) {
    Map<String, String> carrier = new HashMap<>();
    GlobalOpenTelemetry.getPropagators().getTextMapPropagator()
        .inject(Context.current(), carrier, Map::put);

    ShouldAllowRequest request = ShouldAllowRequest.newBuilder()
        .setServiceName(serviceName)
        .setTraceContext(TraceContext.newBuilder()
            .setTraceparent(carrier.getOrDefault("traceparent", ""))
            .setTracestate(carrier.getOrDefault("tracestate", ""))
            .build())
        .build();

    return stub.shouldAllow(request).getAllowed();
}
```

---

## 12. 사이드카 전용 메트릭

### 12.1 메트릭 정의

**코드 근거** (`metrics/prometheus.py` 패턴):
```python
class SelfHealingMetrics:
    self.circuit_breaker_state = Gauge(...)
```

```python
# metrics/sidecar_metrics.py (신규)
from prometheus_client import Counter, Gauge, Histogram

# 연결 메트릭
selfhealing_sidecar_ipc_active_connections = Gauge(
    "selfhealing_sidecar_ipc_active_connections",
    "Active IPC connections",
    ["transport"]  # "uds", "grpc"
)

# 큐 깊이 메트릭
selfhealing_sidecar_uds_queue_depth = Gauge(
    "selfhealing_sidecar_uds_queue_depth",
    "Pending requests in UDS queue",
)

# 지연 시간 메트릭
selfhealing_sidecar_request_latency_seconds = Histogram(
    "selfhealing_sidecar_request_latency_seconds",
    "Request latency in seconds",
    ["method", "transport"],
    buckets=[0.0001, 0.0005, 0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0]
)

# 에러 카운터
selfhealing_sidecar_errors_total = Counter(
    "selfhealing_sidecar_errors_total",
    "Total sidecar errors",
    ["method", "error_type"]
)

# 버퍼 메트릭
selfhealing_sidecar_buffer_entries = Gauge(
    "selfhealing_sidecar_buffer_entries",
    "Entries in persistent buffer",
)
```

### 12.2 Prometheus 스크레이프 설정

```yaml
# prometheus.yml
scrape_configs:
  - job_name: 'selfhealing-sidecar'
    static_configs:
      - targets: ['localhost:9091']  # 사이드카 메트릭 포트
    metrics_path: /metrics
```

---

## 13. 메타-와치독 통합 (SidecarIPCProbe)

### 13.1 개요

**코드 근거** (`meta/health_probe.py`):
```python
class HealthProbe(ABC):
    @property
    @abstractmethod
    def component_name(self) -> str: ...

    @abstractmethod
    def probe(self) -> ProbeResult: ...
```

### 13.2 SidecarIPCProbe 설계

```python
# meta/probes/sidecar_ipc_probe.py (신규)
import os
import socket
from selfhealing.meta.health_probe import HealthProbe, ProbeResult, HealthStatus

class SidecarIPCProbe(HealthProbe):
    """
    사이드카 IPC 건강 프로브.

    확인 항목:
    - UDS 소켓 파일 존재 여부
    - gRPC 포트 응답성
    - 연결 테스트
    """

    SOCKET_PATH = "/tmp/selfhealing.sock"
    GRPC_PORT = 50051

    @property
    def component_name(self) -> str:
        return "sidecar_ipc"

    def probe(self) -> ProbeResult:
        start = time.time()
        details = {}
        status = HealthStatus.HEALTHY
        error = None

        # 1. UDS 소켓 파일 존재 확인
        uds_exists = os.path.exists(self.SOCKET_PATH)
        details["uds_socket_exists"] = uds_exists

        if not uds_exists:
            status = HealthStatus.UNHEALTHY
            error = f"UDS socket not found: {self.SOCKET_PATH}"
        else:
            # 2. UDS 연결 테스트
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.settimeout(1.0)
                sock.connect(self.SOCKET_PATH)
                sock.close()
                details["uds_connectable"] = True
            except Exception as e:
                details["uds_connectable"] = False
                details["uds_error"] = str(e)
                status = HealthStatus.DEGRADED

        # 3. gRPC 포트 응답성 확인
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1.0)
            result = sock.connect_ex(('localhost', self.GRPC_PORT))
            sock.close()
            details["grpc_port_open"] = (result == 0)
            if result != 0:
                status = HealthStatus.DEGRADED
        except Exception as e:
            details["grpc_error"] = str(e)

        return ProbeResult(
            component=self.component_name,
            status=status,
            latency_ms=(time.time() - start) * 1000,
            timestamp=datetime.now(timezone.utc),
            details=details,
            error=error,
        )
```

### 13.3 HealthProbeManager 등록

```python
# meta/health_probe.py 확장
from selfhealing.meta.probes.sidecar_ipc_probe import SidecarIPCProbe

class HealthProbeManager:
    def __init__(self):
        self._probes = {
            "circuit_breaker": CircuitBreakerProbe(),
            "dlq": DLQProbe(),
            "redis": RedisProbe(),
            "sidecar_ipc": SidecarIPCProbe(),  # 신규
        }
```

---

## 14. 서킷 브레이커 상태 캐시 (CBStateCache)

### 14.1 개요

**코드 근거** (`settings/dashboard.py`):
```python
cache_ttl_seconds: int = Field(default=30, ...)
health_penalty_cache_ttl: float = Field(default=5.0, ...)
```

매번 Redis 조회 대신 로컬 TTL 캐시 + 이벤트 기반 무효화 전략을 사용합니다.

### 14.2 CBStateCache 설계

```python
# adapters/ipc/cb_state_cache.py (신규)
import time
import threading
from dataclasses import dataclass
from typing import Any

@dataclass
class CacheEntry:
    value: Any
    expires_at: float

class CBStateCache:
    """
    Circuit Breaker 상태 로컬 캐시.

    전략:
    - TTL 기반 만료 (기본 5초)
    - EventBus 상태 변경 이벤트로 즉시 무효화

    코드 근거: settings/dashboard.py - cache_ttl_* 패턴
    """

    DEFAULT_TTL_SECONDS = 5.0

    def __init__(self, ttl_seconds: float = DEFAULT_TTL_SECONDS):
        self._cache: dict[str, CacheEntry] = {}
        self._lock = threading.RLock()
        self._ttl = ttl_seconds
        self._register_invalidation_handlers()

    def _register_invalidation_handlers(self):
        """EventBus 이벤트로 캐시 무효화"""
        try:
            from selfhealing.services.event_bus import get_event_bus, EventType
            bus = get_event_bus()

            # CB 상태 변경 이벤트 구독
            for event_type in [
                EventType.CIRCUIT_BREAKER_OPENED,
                EventType.CIRCUIT_BREAKER_CLOSED,
                EventType.CIRCUIT_BREAKER_HALF_OPENED,
            ]:
                bus.subscribe(event_type, self._on_state_change)
        except Exception:
            pass  # EventBus 없어도 TTL로 동작

    def _on_state_change(self, event):
        """이벤트 기반 즉시 무효화"""
        service_name = event.data.get("service_name")
        if service_name:
            self.invalidate(service_name)

    def get(self, service_name: str) -> tuple[Any, bool]:
        """캐시 조회. (값, 히트여부) 반환"""
        with self._lock:
            entry = self._cache.get(service_name)
            if entry and entry.expires_at > time.time():
                return entry.value, True
            return None, False

    def set(self, service_name: str, value: Any):
        """캐시 저장"""
        with self._lock:
            self._cache[service_name] = CacheEntry(
                value=value,
                expires_at=time.time() + self._ttl
            )

    def invalidate(self, service_name: str):
        """특정 서비스 캐시 무효화"""
        with self._lock:
            self._cache.pop(service_name, None)

    def invalidate_all(self):
        """전체 캐시 무효화"""
        with self._lock:
            self._cache.clear()
```

### 14.3 UDS 서버 통합

```python
class UDSServer:
    def __init__(self):
        self._cb_cache = CBStateCache(ttl_seconds=5.0)
        # ...

    def _cb_should_allow(self, service_name: str) -> dict:
        # 1. 캐시 확인
        cached, hit = self._cb_cache.get(service_name)
        if hit:
            return cached

        # 2. 실제 서비스 호출
        result = {
            "allowed": self.cb_service.should_allow(service_name),
            "state": self.cb_service.get_state(service_name),
        }

        # 3. 캐시 저장
        self._cb_cache.set(service_name, result)

        return result
```

---

## 15. 사이드카 인증 (Static Bearer Token)

### 15.1 개요

**코드 근거** (`services/http_client.py`):
```python
client = SelfHealingHttpClient(base_headers={"Authorization": "Bearer xxx"})
```

초기 구현 단계에서 Static Bearer Token 인증을 사용합니다.

### 15.2 설정

```yaml
# sidecar-config.yaml
auth:
  enabled: true
  method: "bearer_token"
  token: "${SIDECAR_AUTH_TOKEN}"  # 환경변수에서 로드

# 환경변수 예시
SIDECAR_AUTH_TOKEN=sk-selfhealing-abc123xyz789
```

### 15.3 서버 측 검증

```python
# adapters/ipc/auth.py (신규)
import os
import hmac

class SidecarAuthenticator:
    """
    Static Bearer Token 인증.

    코드 근거: services/http_client.py - "Authorization": "Bearer xxx"
    """

    def __init__(self):
        self._token = os.environ.get("SIDECAR_AUTH_TOKEN", "")
        self._enabled = bool(self._token)

    def validate(self, provided_token: str) -> bool:
        """토큰 검증 (timing-safe 비교)"""
        if not self._enabled:
            return True
        return hmac.compare_digest(self._token, provided_token)
```

### 15.4 클라이언트 SDK 가이드

**Go 클라이언트**:
```go
func NewAuthenticatedClient(socketPath, token string) (*Client, error) {
    client, err := NewClient(socketPath)
    if err != nil {
        return nil, err
    }
    client.authToken = token
    return client, nil
}

func (c *Client) sendRequest(request map[string]interface{}) error {
    // 인증 헤더 추가
    if c.authToken != "" {
        request["auth"] = map[string]string{
            "token": c.authToken,
        }
    }
    // ...
}
```

**gRPC Metadata 인증**:
```go
md := metadata.Pairs("authorization", "Bearer "+token)
ctx := metadata.NewOutgoingContext(ctx, md)
response, err := stub.ShouldAllow(ctx, request)
```

---

## 16. Service Mesh (Istio) 배포 가이드

### 16.1 포트 할당 지침

Istio Envoy 사이드카와 Self-Healing 사이드카가 동일 Pod에서 공존할 때:

| 컴포넌트 | 포트 | 프로토콜 | 용도 |
|----------|------|----------|------|
| Envoy | 15001 | TCP | Outbound |
| Envoy | 15006 | TCP | Inbound |
| Self-Healing gRPC | **50051** | HTTP/2 | IPC |
| Self-Healing Metrics | **9091** | HTTP | Prometheus |
| Self-Healing Admin | **9092** | HTTP | 관리 API |

### 16.2 리소스 Limit 권고

```yaml
# k8s deployment
spec:
  containers:
    - name: selfhealing-sidecar
      resources:
        requests:
          memory: "64Mi"
          cpu: "50m"
        limits:
          memory: "128Mi"
          cpu: "200m"

    # Istio sidecar는 자동 주입됨
    # 총 Pod 리소스 = App + Self-Healing + Istio
```

### 16.3 Port Exclusion 설정

```yaml
# Istio가 Self-Healing 트래픽을 가로채지 않도록 제외
metadata:
  annotations:
    traffic.sidecar.istio.io/excludeOutboundPorts: "50051"
    traffic.sidecar.istio.io/excludeInboundPorts: "50051,9091,9092"
```

### 16.4 mTLS 중복 방지

```yaml
# Self-Healing은 localhost 통신이므로 TLS 불필요
# Istio mTLS는 외부 통신에만 적용
auth:
  grpc:
    tls:
      enabled: false  # localhost에서는 비활성화
```

---

## 17. 사이드카 장애 시 폴백 정책

### 17.1 Fail-Open 정책

**코드 근거** (`tests/self_healing/django/test_audit_middleware.py`):
```python
def test_middleware_fail_open_policy(self):
    """Fail-Open: 기록 실패해도 응답은 정상 반환."""
```

사이드카 통신 실패 시 **모든 요청을 허용** (Fail-Open)합니다.

### 17.2 Go SDK 구현

```go
type FailOpenClient struct {
    *Client
    fallbackQueue chan Request
    maxQueueSize  int
}

func NewFailOpenClient(socketPath string) (*FailOpenClient, error) {
    client, err := NewClient(socketPath)
    // 연결 실패해도 계속 진행

    return &FailOpenClient{
        Client:        client,
        fallbackQueue: make(chan Request, 1000),
        maxQueueSize:  1000,
    }, nil
}

func (c *FailOpenClient) ShouldAllow(serviceName string) bool {
    if c.Client == nil || !c.isConnected() {
        // Fail-Open: 사이드카 없으면 모두 허용
        return true
    }

    result, err := c.Client.ShouldAllow(serviceName)
    if err != nil {
        // 통신 오류 시 Fail-Open
        log.Printf("[SelfHealing SDK] Sidecar error, fail-open: %v", err)
        return true
    }

    return result
}

func (c *FailOpenClient) StoreWithFallback(entry Entry) error {
    err := c.Store(entry)
    if err != nil {
        // 실패 시 인메모리 큐에 임시 보관
        select {
        case c.fallbackQueue <- entry:
            log.Printf("[SelfHealing SDK] Queued for retry: %s", entry.ID)
        default:
            log.Printf("[SelfHealing SDK] Queue full, dropping: %s", entry.ID)
        }
    }
    return err
}

func (c *FailOpenClient) FlushFallbackQueue() {
    for {
        select {
        case entry := <-c.fallbackQueue:
            if err := c.Store(entry); err == nil {
                log.Printf("[SelfHealing SDK] Flushed: %s", entry.ID)
            } else {
                // 다시 큐에 넣기
                c.fallbackQueue <- entry
                return  // 다음 주기에 재시도
            }
        default:
            return
        }
    }
}
```

### 17.3 Java SDK 구현

```java
public class FailOpenSelfHealingClient {
    private final SelfHealingClient delegate;
    private final BlockingQueue<Entry> fallbackQueue;
    private final AtomicBoolean connected = new AtomicBoolean(false);

    public boolean shouldAllow(String serviceName) {
        if (!connected.get()) {
            // Fail-Open: 사이드카 없으면 모두 허용
            return true;
        }

        try {
            return delegate.shouldAllow(serviceName);
        } catch (StatusRuntimeException e) {
            // gRPC 오류 시 Fail-Open
            log.warn("Sidecar error, fail-open", e);
            connected.set(false);
            scheduleReconnect();
            return true;
        }
    }

    public void storeWithFallback(Entry entry) {
        try {
            delegate.store(entry);
        } catch (Exception e) {
            // 실패 시 인메모리 큐에 임시 보관
            if (!fallbackQueue.offer(entry)) {
                log.error("Fallback queue full, dropping entry");
            }
        }
    }
}
```

---

## 18. Shared Memory 기반 CB 상태 공유 (CBStateSnapshot)

### 18.1 개요

**코드 근거** (`audit/persistence/mmap_buffer.py`):
```python
class MmapBuffer:
    """mmap 기반 간단한 영속 버퍼. 표준 라이브러리만 사용."""

    def __init__(self, file_path: str | Path | None = None, size_mb: int = 100):
        self._mmap: mmap.mmap | None = None
```

초저지연이 필수적인 `ShouldAllow` 읽기 작업을 위해, UDS보다 빠른 **Shared Memory** 기반 상태 공유를 제공합니다.

### 18.2 네이밍 선택: `CBStateSnapshot`

| 후보 | 선택 이유 |
|------|-----------|
| ~~SharedMemory~~ | 너무 일반적, 용도 불명확 |
| ~~LocalCBCache~~ | "캐시"는 TTL 기반을 암시, 여기선 스냅샷 |
| **CBStateSnapshot** ✅ | Circuit Breaker 전용 + 읽기 전용 스냅샷 역할 명확 |

**선택 근거**:
- 기존 `MmapBuffer`는 "영속화" 목적, 이 컴포넌트는 "실시간 공유" 목적
- "Snapshot"은 사이드카가 주기적으로 쓰고, 클라이언트가 읽기만 하는 구조를 명확히 표현
- 기존 코드의 `CircuitBreakerState*` 네이밍 패턴과 일관성 유지

### 18.3 아키텍처

```
┌─────────────────────────────────────────────────────────────────┐
│                       Same Pod / Host                            │
│                                                                  │
│  ┌─────────────┐                      ┌─────────────────────┐   │
│  │  Go App     │                      │  Sidecar            │   │
│  │             │                      │                     │   │
│  │  ┌───────┐  │   /dev/shm/         │  ┌───────────────┐  │   │
│  │  │mmap   │  │   selfhealing_cb    │  │ CBStateWriter │  │   │
│  │  │Reader │◄─┼──────────────────────┼──│ (100ms 주기)  │  │   │
│  │  └───────┘  │     (Zero-Copy)     │  │               │  │   │
│  │             │                      │  │ ┌───────────┐ │  │   │
│  │  Latency:   │                      │  │ │EventBus   │ │  │   │
│  │  ~10μs      │                      │  │ │Subscriber │ │  │   │
│  └─────────────┘                      │  │ └───────────┘ │  │   │
│                                       │  └───────────────┘  │   │
│                                       └─────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘

특징:
- Write: 사이드카만 (100ms 주기 + EventBus 이벤트 시 즉시)
- Read: Go/Java 앱 (Zero-Copy mmap)
- Latency: UDS ~150μs → Shared Memory ~10μs (15배 개선)
```

### 18.4 파일 포맷

```
/dev/shm/selfhealing_cb_snapshot (64KB)

Offset  Size   Field
------  ----   -----
0       4      Magic ("CBSN")
4       2      Version (1)
6       2      Service Count
8       8      Last Updated (Unix timestamp, microseconds)
16      4      Checksum (CRC32)
20      -      Reserved (12 bytes)

32      -      Service Entries (최대 1000개)
               ├─ service_name: 64 bytes (null-padded)
               ├─ state: 1 byte (0=closed, 1=open, 2=half_open)
               ├─ failure_count: 4 bytes
               ├─ success_count: 4 bytes
               ├─ opened_at: 8 bytes (Unix timestamp)
               └─ reserved: 3 bytes
               Total: 84 bytes per entry
```

### 18.5 사이드카 Writer 구현

```python
# adapters/ipc/cb_state_snapshot.py (신규)
import mmap
import struct
import time
import threading
from pathlib import Path

class CBStateSnapshotWriter:
    """
    Circuit Breaker 상태를 Shared Memory에 기록.

    코드 근거: audit/persistence/mmap_buffer.py - MmapBuffer 패턴
    """

    MAGIC = b"CBSN"
    VERSION = 1
    HEADER_SIZE = 32
    ENTRY_SIZE = 84
    MAX_SERVICES = 1000

    SHM_PATH = Path("/dev/shm/selfhealing_cb_snapshot")

    def __init__(self, update_interval_ms: int = 100):
        self._update_interval = update_interval_ms / 1000
        self._lock = threading.Lock()
        self._running = False
        self._init_shm()
        self._register_event_handlers()

    def _init_shm(self):
        """Shared Memory 파일 초기화"""
        size = self.HEADER_SIZE + (self.ENTRY_SIZE * self.MAX_SERVICES)

        with open(self.SHM_PATH, "w+b") as f:
            f.write(b"\x00" * size)

        self._file = open(self.SHM_PATH, "r+b")
        self._mmap = mmap.mmap(self._file.fileno(), 0)

        # Header 쓰기
        self._write_header(service_count=0)

    def _register_event_handlers(self):
        """EventBus 이벤트로 즉시 업데이트"""
        from selfhealing.services.event_bus import get_event_bus, EventType

        bus = get_event_bus()
        for event_type in [
            EventType.CIRCUIT_BREAKER_OPENED,
            EventType.CIRCUIT_BREAKER_CLOSED,
            EventType.CIRCUIT_BREAKER_HALF_OPENED,
        ]:
            bus.subscribe(event_type, lambda e: self.update_snapshot())

    def update_snapshot(self):
        """전체 CB 상태 스냅샷 업데이트"""
        from selfhealing.services.circuit_breaker import get_circuit_breaker_service

        cb_service = get_circuit_breaker_service()
        states = cb_service.get_all_states()

        with self._lock:
            offset = self.HEADER_SIZE
            for i, state in enumerate(states[:self.MAX_SERVICES]):
                self._write_entry(offset, state)
                offset += self.ENTRY_SIZE

            self._write_header(service_count=len(states))
            self._mmap.flush()

    def _write_header(self, service_count: int):
        """헤더 쓰기"""
        timestamp_us = int(time.time() * 1_000_000)
        header = struct.pack(
            ">4sHHQI12x",
            self.MAGIC,
            self.VERSION,
            service_count,
            timestamp_us,
            0,  # checksum (나중에 계산)
        )
        self._mmap[:self.HEADER_SIZE] = header

    def _write_entry(self, offset: int, state: dict):
        """서비스 엔트리 쓰기"""
        state_byte = {"closed": 0, "open": 1, "half_open": 2}.get(state["state"], 0)
        entry = struct.pack(
            ">64sBII8x3x",
            state["service_name"].encode()[:64].ljust(64, b"\x00"),
            state_byte,
            state.get("failure_count", 0),
            state.get("success_count", 0),
        )
        self._mmap[offset:offset + self.ENTRY_SIZE] = entry
```

### 18.6 Go 클라이언트 Reader

```go
package selfhealing

import (
    "encoding/binary"
    "os"
    "sync"
    "syscall"
    "unsafe"
)

const (
    ShmPath     = "/dev/shm/selfhealing_cb_snapshot"
    HeaderSize  = 32
    EntrySize   = 84
    MaxServices = 1000
)

type CBStateSnapshot struct {
    data []byte
    mu   sync.RWMutex
}

type ServiceState struct {
    ServiceName  string
    State        string  // "closed", "open", "half_open"
    FailureCount uint32
    SuccessCount uint32
}

func NewCBStateSnapshot() (*CBStateSnapshot, error) {
    f, err := os.Open(ShmPath)
    if err != nil {
        return nil, err
    }
    defer f.Close()

    size := HeaderSize + (EntrySize * MaxServices)
    data, err := syscall.Mmap(int(f.Fd()), 0, size,
        syscall.PROT_READ, syscall.MAP_SHARED)
    if err != nil {
        return nil, err
    }

    return &CBStateSnapshot{data: data}, nil
}

func (s *CBStateSnapshot) ShouldAllow(serviceName string) bool {
    state := s.GetState(serviceName)
    if state == nil {
        return true  // 없으면 허용 (Fail-Open)
    }
    return state.State == "closed" || state.State == "half_open"
}

func (s *CBStateSnapshot) GetState(serviceName string) *ServiceState {
    s.mu.RLock()
    defer s.mu.RUnlock()

    // Header에서 서비스 수 읽기
    serviceCount := binary.BigEndian.Uint16(s.data[6:8])

    // 엔트리 검색
    for i := uint16(0); i < serviceCount; i++ {
        offset := HeaderSize + (int(i) * EntrySize)
        name := string(s.data[offset : offset+64])
        name = strings.TrimRight(name, "\x00")

        if name == serviceName {
            stateMap := map[byte]string{0: "closed", 1: "open", 2: "half_open"}
            return &ServiceState{
                ServiceName:  name,
                State:        stateMap[s.data[offset+64]],
                FailureCount: binary.BigEndian.Uint32(s.data[offset+65 : offset+69]),
                SuccessCount: binary.BigEndian.Uint32(s.data[offset+69 : offset+73]),
            }
        }
    }
    return nil
}
```

### 18.7 사용 시나리오

| 시나리오 | 권장 방식 | 이유 |
|----------|-----------|------|
| 초고빈도 체크 (1000+ RPS) | **Shared Memory** | ~10μs latency |
| 배치 체크 (10+ 서비스) | UDS/gRPC Batch | 단일 IPC 호출 |
| 상태 변경 (force_open 등) | gRPC | 쓰기 작업은 IPC 필수 |
| 이벤트 구독 | gRPC Streaming | 푸시 알림 필요 |

---

## 19. SDK 코드 생성 자동화

### 19.1 개요

**코드 근거** (`pyproject.toml`):
```toml
# gRPC & Protobuf
"grpcio>=1.76.0",
"protobuf>=6.33.4",
"googleapis-common-protos>=1.72.0",
```

Proto 정의를 기반으로 Go, Java, Python SDK를 자동 생성하고 배포합니다.

### 19.2 디렉토리 구조

```
selfhealing/
├── proto/
│   └── selfhealing/
│       └── v1/
│           ├── sidecar.proto          # 메인 서비스 정의
│           ├── circuit_breaker.proto  # CB 메시지
│           ├── dlq.proto              # DLQ 메시지
│           ├── buffer.proto           # Buffer 메시지
│           └── events.proto           # 이벤트 스트리밍
│
├── sdks/
│   ├── go/
│   │   ├── go.mod
│   │   ├── selfhealing/
│   │   │   ├── client.go
│   │   │   └── generated/             # protoc 생성
│   │   └── README.md
│   │
│   ├── java/
│   │   ├── pom.xml
│   │   ├── src/main/java/
│   │   │   └── com/selfhealing/
│   │   │       ├── SelfHealingClient.java
│   │   │       └── generated/         # protoc 생성
│   │   └── README.md
│   │
│   └── python/
│       ├── pyproject.toml
│       ├── selfhealing_sdk/
│       │   ├── client.py
│       │   └── generated/             # protoc 생성
│       └── README.md
```

### 19.3 Protobuf 스키마 (통합)

```protobuf
// proto/selfhealing/v1/sidecar.proto
syntax = "proto3";

package selfhealing.v1;

option go_package = "github.com/myproject/selfhealing-go/v1";
option java_package = "com.selfhealing.v1";
option java_multiple_files = true;

import "selfhealing/v1/circuit_breaker.proto";
import "selfhealing/v1/dlq.proto";
import "selfhealing/v1/buffer.proto";
import "selfhealing/v1/events.proto";

// 통합 서비스 정의
service SelfHealingService {
  // Circuit Breaker
  rpc ShouldAllow(ShouldAllowRequest) returns (ShouldAllowResponse);
  rpc ShouldAllowBatch(ShouldAllowBatchRequest) returns (ShouldAllowBatchResponse);
  rpc ForceOpen(ForceOpenRequest) returns (CircuitBreakerResult);
  rpc ForceClose(ForceCloseRequest) returns (CircuitBreakerResult);

  // DLQ
  rpc StoreDLQ(StoreDLQRequest) returns (StoreDLQResponse);
  rpc ReplayDLQ(ReplayDLQRequest) returns (ReplayDLQResponse);

  // Buffer
  rpc BufferStore(BufferStoreRequest) returns (BufferStoreResponse);
  rpc BufferFlush(BufferFlushRequest) returns (BufferFlushResponse);

  // Event Streaming
  rpc SubscribeEvents(EventSubscription) returns (stream SelfHealingEvent);
}
```

### 19.4 Makefile 타겟

```makefile
# Makefile 추가 타겟

# =============================================================================
# Proto & SDK 생성
# =============================================================================

PROTO_DIR := proto/selfhealing/v1
SDK_DIR := sdks

.PHONY: proto-lint proto-breaking proto-generate sdk-go sdk-java sdk-python sdk-all

# Proto 린트 (buf 사용)
proto-lint:
	@echo "🔍 Proto 린트 검사..."
	buf lint $(PROTO_DIR)

# Proto Breaking Change 검사
proto-breaking:
	@echo "⚠️ Breaking Change 검사..."
	buf breaking $(PROTO_DIR) --against '.git#branch=main'

# Proto 코드 생성 (전체)
proto-generate: proto-lint
	@echo "📦 Proto 코드 생성..."

	# Python
	python -m grpc_tools.protoc \
		-I=$(PROTO_DIR) \
		--python_out=$(SDK_DIR)/python/selfhealing_sdk/generated \
		--grpc_python_out=$(SDK_DIR)/python/selfhealing_sdk/generated \
		$(PROTO_DIR)/*.proto

	# Go
	protoc \
		-I=$(PROTO_DIR) \
		--go_out=$(SDK_DIR)/go/selfhealing/generated \
		--go-grpc_out=$(SDK_DIR)/go/selfhealing/generated \
		$(PROTO_DIR)/*.proto

	# Java
	protoc \
		-I=$(PROTO_DIR) \
		--java_out=$(SDK_DIR)/java/src/main/java \
		--grpc-java_out=$(SDK_DIR)/java/src/main/java \
		$(PROTO_DIR)/*.proto

	@echo "✅ Proto 생성 완료"

# SDK 빌드 & 테스트
sdk-go:
	@echo "🐹 Go SDK 빌드..."
	cd $(SDK_DIR)/go && go mod tidy && go test ./...

sdk-java:
	@echo "☕ Java SDK 빌드..."
	cd $(SDK_DIR)/java && mvn clean package

sdk-python:
	@echo "🐍 Python SDK 빌드..."
	cd $(SDK_DIR)/python && pip install -e . && pytest

sdk-all: proto-generate sdk-go sdk-java sdk-python
	@echo "✅ 모든 SDK 빌드 완료"
```

### 19.5 CI/CD 파이프라인 (GitHub Actions)

```yaml
# .github/workflows/sdk-release.yml
name: SDK Release

on:
  push:
    tags:
      - 'sdk-v*'

jobs:
  lint-and-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0  # Breaking change 검사용

      - name: Setup Buf
        uses: bufbuild/buf-setup-action@v1

      - name: Lint Protos
        run: buf lint proto/selfhealing/v1

      - name: Check Breaking Changes
        run: buf breaking proto/selfhealing/v1 --against '.git#branch=main'

  generate-and-publish:
    needs: lint-and-check
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Setup Tools
        run: |
          # protoc 설치
          apt-get install -y protobuf-compiler
          # Go protoc 플러그인
          go install google.golang.org/protobuf/cmd/protoc-gen-go@latest
          go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@latest

      - name: Generate SDKs
        run: make proto-generate

      - name: Extract Version
        id: version
        run: echo "VERSION=${GITHUB_REF#refs/tags/sdk-v}" >> $GITHUB_OUTPUT

      # Go SDK 배포
      - name: Publish Go SDK
        run: |
          cd sdks/go
          git tag v${{ steps.version.outputs.VERSION }}
          git push origin v${{ steps.version.outputs.VERSION }}

      # Java SDK 배포 (Maven Central)
      - name: Publish Java SDK
        run: |
          cd sdks/java
          mvn versions:set -DnewVersion=${{ steps.version.outputs.VERSION }}
          mvn deploy -P release
        env:
          MAVEN_USERNAME: ${{ secrets.MAVEN_USERNAME }}
          MAVEN_PASSWORD: ${{ secrets.MAVEN_PASSWORD }}

      # Python SDK 배포 (PyPI)
      - name: Publish Python SDK
        run: |
          cd sdks/python
          pip install build twine
          python -m build
          twine upload dist/*
        env:
          TWINE_USERNAME: __token__
          TWINE_PASSWORD: ${{ secrets.PYPI_TOKEN }}
```

### 19.6 버전 관리 전략

| 변경 유형 | 버전 증가 | 예시 |
|----------|----------|------|
| 새 필드 추가 (optional) | Patch | 1.0.0 → 1.0.1 |
| 새 RPC 메서드 추가 | Minor | 1.0.0 → 1.1.0 |
| 필드 삭제/이름 변경 | **Major** | 1.0.0 → **2.0.0** |
| 필드 번호 변경 | **Major** | Breaking Change |

### 19.7 하위 호환성 보장

```protobuf
// ❌ 금지: 필드 번호 변경
message Request {
  // string name = 1;  // 삭제 금지
  string service_name = 2;  // 새 이름은 새 번호로
}

// ✅ 권장: 새 필드는 새 번호로
message Request {
  string service_name = 1;
  optional string namespace = 2;  // 신규 추가 (optional)
  reserved 3;  // 미래 사용 예약
}
```

---

## 20. 관련 문서

| 문서 번호 | 제목 |
|-----------|------|
| [176](176_DISK_PERSISTENT_BUFFER.md) | Disk Persistent Buffer |
| [177](177_META_WATCHDOG.md) | Meta-Watchdog |
| [183](183_SIDECAR_PATTERN_OVERVIEW.md) | 사이드카 패턴 개요 |
| [185](185_SIDECAR_PERFORMANCE_ANALYSIS.md) | 성능 분석 |
| [186](186_SIDECAR_IMPLEMENTATION_ROADMAP.md) | 구현 로드맵 |
